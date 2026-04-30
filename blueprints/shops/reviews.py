import datetime
from flask import Blueprint, request, jsonify
from bson import ObjectId
from utilities import auth, verify
from flask import send_file
import io
from PIL import Image

reviews_blueprint = Blueprint('reviews', __name__)


@reviews_blueprint.route("/", methods=['POST', 'PUT'])
@auth.is_user
def upsert_review(*args, **kwargs):
    """Create or update a review"""
    user_id = kwargs.get('user_id')
    shop_id = request.args.get('shop_id')
    message = request.args.get('message')
    score = request.args.get('score')

    # Validate args
    corrections = []

    if not shop_id:
        corrections.append({"shop_id": "shop_id was not given"})

    message_corrections = verify.check_review(message)
    if message_corrections:
        corrections.append({"message": message_corrections})

    score_corrections = verify.check_review_score(score)
    if score_corrections:
        corrections.append({"score": score_corrections})

    if len(corrections) > 0:
        return jsonify({"corrections": corrections}), 400

    shops_collection = auth.create_collection_connection("Shops")

    # Verify shop exists
    shop = shops_collection.find_one({"_id": ObjectId(shop_id), "deleted": False})
    if not shop:
        return jsonify({"error": "Shop not found"}), 404

    # Check if review exists
    review_from_shop = shops_collection.find_one(
        {"_id": ObjectId(shop_id), "deleted": False, "reviews.user_id": ObjectId(user_id), "reviews.deleted": False},
        {"_id": 1, "reviews.$": 1}
    )

    current_time = datetime.datetime.now(tz=datetime.timezone.utc)

    if review_from_shop:
        # Update existing review - soft delete old and add new
        old_review = review_from_shop['reviews'][0]

        # Soft delete old review
        shops_collection.update_one(
            {"_id": ObjectId(shop_id), "reviews.user_id": ObjectId(user_id), "reviews.deleted": False},
            {"$set": {"reviews.$.deleted": True}}
        )

        # Create new review with edit history
        review = {
            "user_id": ObjectId(user_id),
            "message": message,
            "score": int(score),
            "date_created": old_review['date_created'],
            "date_edited": current_time,
            "edits": old_review.get('edits', []) + [{
                "message": old_review['message'],
                "score": old_review['score'],
                "date": old_review.get('date_edited', old_review['date_created'])
            }],
            "likes": old_review.get('likes', {}),
            "deleted": False,
            "photo": old_review.get('photo'),
        }

        shops_collection.update_one(
            {"_id": ObjectId(shop_id)},
            {"$push": {"reviews": review}}
        )
        return_message = "edited"
    else:
        # Create new review
        review = {
            "user_id": ObjectId(user_id),
            "message": message,
            "score": int(score),
            "date_created": current_time,
            "date_edited": current_time,
            "edits": [],
            "likes": {},
            "deleted": False,
            "photo": None,
        }

        shops_collection.update_one(
            {"_id": ObjectId(shop_id)},
            {"$push": {"reviews": review}}
        )
        return_message = "created"

    return jsonify({"message": f"Review {return_message} successfully"}), 200


@reviews_blueprint.route("/", methods=['GET'])
def get_reviews():
    """Get reviews for a shop with filters and pagination"""
    shop_id = request.args.get('shop_id')
    user_id = request.args.get('user_id')
    min_score = int(request.args.get('min_score', 1))
    max_score = int(request.args.get('max_score', 5))
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))

    if not shop_id:
        return jsonify({"error": "shop_id is required"}), 400

    try:
        shop_id_obj = ObjectId(shop_id)
        user_id_obj = ObjectId(user_id) if user_id else None
    except:
        return jsonify({"error": "Invalid ID format"}), 400

    shops_collection = auth.create_collection_connection("Shops")

    # Build match conditions for reviews
    review_match = {"$eq": [{"$ifNull": ["$$review.deleted", False]}, False]}

    if user_id_obj:
        review_match = {
            "$and": [
                review_match,
                {"$eq": ["$$review.user_id", user_id_obj]}
            ]
        }

    if min_score > 1:
        current_match = review_match
        review_match = {
            "$and": [
                current_match,
                {"$gte": ["$$review.score", min_score]}
            ]
        }

    if max_score < 5:
        current_match = review_match
        review_match = {
            "$and": [
                current_match,
                {"$lte": ["$$review.score", max_score]}
            ]
        }

    pipeline = [
        {"$match": {"_id": shop_id_obj, "deleted": False}},
        {
            "$project": {
                "reviews": {
                    "$filter": {
                        "input": "$reviews",
                        "as": "review",
                        "cond": review_match
                    }
                }
            }
        },
        {"$unwind": "$reviews"},
        {
            "$addFields": {
                "reviews.like_count": {"$size": {"$objectToArray": "$reviews.likes"}}
            }
        },
        {"$sort": {"reviews.like_count": -1, "reviews.date_created": -1}},
        {"$skip": (page - 1) * per_page},
        {"$limit": per_page},
        {
            "$project": {
                "_id": 0,
                "review": "$reviews"
            }
        }
    ]

    reviews = list(shops_collection.aggregate(pipeline))

    # Convert ObjectIds to strings for JSON serialization and remove photo
    for r in reviews:
        if 'review' in r:
            if 'user_id' in r['review']:
                r['review']['user_id'] = str(r['review']['user_id'])
            if 'date_created' in r['review']:
                r['review']['date_created'] = r['review']['date_created'].isoformat()
            if 'date_edited' in r['review']:
                r['review']['date_edited'] = r['review']['date_edited'].isoformat()
            if 'edits' in r['review']:
                for edit in r['review']['edits']:
                    if 'date' in edit:
                        edit['date'] = edit['date'].isoformat()
            # Remove photo from response - use separate endpoint to get photos
            if 'photo' in r['review']:
                del r['review']['photo']


    return jsonify({
        "reviews": [r['review'] for r in reviews],
        "page": page,
        "per_page": per_page,
        "count": len(reviews)
    }), 200


@reviews_blueprint.route("/like", methods=['POST'])
@auth.is_user
def like_review(*args, **kwargs):
    """Like a review"""
    user_id = kwargs.get('user_id')
    shop_id = request.args.get('shop_id')
    review_user_id = request.args.get('review_user_id')

    if not shop_id or not review_user_id:
        return jsonify({"error": "shop_id and review_user_id are required"}), 400

    try:
        shop_id_obj = ObjectId(shop_id)
        review_user_id_obj = ObjectId(review_user_id)
        user_id_obj = ObjectId(user_id)
    except:
        return jsonify({"error": "Invalid ID format"}), 400

    shops_collection = auth.create_collection_connection("Shops")

    # Add like using dot notation for the likes object
    result = shops_collection.update_one(
        {
            "_id": shop_id_obj,
            "reviews.user_id": review_user_id_obj,
            "reviews.deleted": False
        },
        {
            "$set": {
                f"reviews.$[review].likes.{user_id}": True
            }
        },
        array_filters=[{"review.user_id": review_user_id_obj, "review.deleted": False}]
    )

    if result.matched_count == 0:
        return jsonify({"error": "Review not found"}), 404

    return jsonify({"message": "Review liked successfully"}), 200


@reviews_blueprint.route("/like", methods=['DELETE'])
@auth.is_user
def dislike_review(*args, **kwargs):
    """Remove like from a review"""
    user_id = kwargs.get('user_id')
    shop_id = request.args.get('shop_id')
    review_user_id = request.args.get('review_user_id')

    if not shop_id or not review_user_id:
        return jsonify({"error": "shop_id and review_user_id are required"}), 400

    try:
        shop_id_obj = ObjectId(shop_id)
        review_user_id_obj = ObjectId(review_user_id)
    except:
        return jsonify({"error": "Invalid ID format"}), 400

    shops_collection = auth.create_collection_connection("Shops")

    # Remove like using $unset
    result = shops_collection.update_one(
        {
            "_id": shop_id_obj,
            "reviews.user_id": review_user_id_obj,
            "reviews.deleted": False
        },
        {
            "$unset": {
                f"reviews.$[review].likes.{user_id}": ""
            }
        },
        array_filters=[{"review.user_id": review_user_id_obj, "review.deleted": False}]
    )

    if result.matched_count == 0:
        return jsonify({"error": "Review not found"}), 404

    if result.modified_count == 0:
        return jsonify({"message": "Like was not present"}), 200

    return jsonify({"message": "Like removed successfully"}), 200


@reviews_blueprint.route("/", methods=['DELETE'])
@auth.is_user
def delete_review(*args, **kwargs):
    """Delete a review (soft delete)"""
    user_id = kwargs.get('user_id')
    is_admin = kwargs.get('is_admin', False)
    shop_id = request.args.get('shop_id')

    # Check if admin is deleting another user's review
    if is_admin and request.args.get('user_id'):
        user_id = request.args.get('user_id')

    if not shop_id:
        return jsonify({"error": "shop_id is required"}), 400

    try:
        shop_id_obj = ObjectId(shop_id)
        user_id_obj = ObjectId(user_id)
    except:
        return jsonify({"error": "Invalid ID format"}), 400

    shops_collection = auth.create_collection_connection("Shops")

    # Use arrayFilters to target the specific review
    result = shops_collection.update_one(
        {
            "_id": shop_id_obj,
            "reviews": {
                "$elemMatch": {
                    "user_id": user_id_obj,
                    "deleted": False
                }
            }
        },
        {
            "$set": {"reviews.$[review].deleted": True}
        },
        array_filters=[
            {
                "review.user_id": user_id_obj,
                "review.deleted": False
            }
        ]
    )

    if result.modified_count == 0:
        return jsonify({"error": "Review not found or already deleted"}), 404

    return jsonify({"message": "Review deleted successfully"}), 200


@reviews_blueprint.route("/photo", methods=['PUT'])
@auth.is_user
def update_review_photo(*args, **kwargs):
    """Upload or update review photo"""

    user_id = kwargs.get('user_id')
    shop_id = request.args.get('shop_id')
    photo = request.files.get('photo')

    if not shop_id:
        return jsonify({"error": "shop_id is required"}), 400

    if not photo:
        return jsonify({"error": "No photo provided"}), 400

    try:
        shop_id_obj = ObjectId(shop_id)
        user_id_obj = ObjectId(user_id)
    except:
        return jsonify({"error": "Invalid ID format"}), 400

    try:
        # Convert photo to JPEG
        photo_jpeg = Image.open(photo).convert('RGB')

        # Resize photo (returns new image, doesn't modify in place)
        photo_jpeg = photo_jpeg.resize((256, 256))

        # Save to bytes
        img_io = io.BytesIO()
        photo_jpeg.save(img_io, format='JPEG', quality=85)
        photo_bytes = img_io.getvalue()
    except Exception as e:
        return jsonify({"error": f"Error processing image: {str(e)}"}), 400

    shops_collection = auth.create_collection_connection("Shops")

    # Verify user owns this review
    review = shops_collection.find_one(
        {
            "_id": shop_id_obj,
            "deleted": False,
            "reviews": {
                "$elemMatch": {
                    "user_id": user_id_obj,
                    "deleted": False
                }
            }
        },
        {"_id": 1}
    )

    if not review:
        return jsonify({"error": "Review not found"}), 404

    # Store binary data using arrayFilters
    result = shops_collection.update_one(
        {
            "_id": shop_id_obj,
            "reviews": {
                "$elemMatch": {
                    "user_id": user_id_obj,
                    "deleted": False
                }
            }
        },
        {
            "$set": {"reviews.$[review].photo": photo_bytes}
        },
        array_filters=[
            {
                "review.user_id": user_id_obj,
                "review.deleted": False
            }
        ]
    )

    if result.modified_count == 0:
        return jsonify({"error": "Failed to update photo"}), 500

    return jsonify({"message": "Review photo updated successfully"}), 200


@reviews_blueprint.route("/photo", methods=['GET'])
def get_review_photo():
    """Get review photo"""

    shop_id = request.args.get('shop_id')
    review_user_id = request.args.get('review_user_id')

    if not shop_id or not review_user_id:
        return jsonify({"error": "shop_id and review_user_id are required"}), 400

    try:
        shop_id_obj = ObjectId(shop_id)
        review_user_id_obj = ObjectId(review_user_id)
    except:
        return jsonify({"error": "Invalid ID format"}), 400

    shops_collection = auth.create_collection_connection("Shops")

    # Get review with photo using $elemMatch
    result = shops_collection.find_one(
        {
            "_id": shop_id_obj,
            "deleted": False,
            "reviews": {
                "$elemMatch": {
                    "user_id": review_user_id_obj,
                    "deleted": False
                }
            }
        },
        {"reviews.$": 1}
    )

    if not result or not result.get('reviews'):
        return jsonify({"error": "Review not found"}), 404

    review = result['reviews'][0]

    if "photo" not in review or review["photo"] is None:
        return jsonify({"error": "Photo not found"}), 404

    return send_file(
        io.BytesIO(review["photo"]),
        mimetype='image/jpeg'
    )


@reviews_blueprint.route("/photo", methods=['DELETE'])
@auth.is_user
def delete_review_photo(*args, **kwargs):
    """Delete review photo"""
    user_id = kwargs.get('user_id')
    is_admin = kwargs.get('is_admin', False)
    shop_id = request.args.get('shop_id')

    # Allow admin to delete any review photo
    if is_admin and request.args.get('review_user_id'):
        user_id = request.args.get('review_user_id')

    if not shop_id:
        return jsonify({"error": "shop_id is required"}), 400

    try:
        shop_id_obj = ObjectId(shop_id)
        user_id_obj = ObjectId(user_id)
    except:
        return jsonify({"error": "Invalid ID format"}), 400

    shops_collection = auth.create_collection_connection("Shops")

    # Check if review exists using $elemMatch
    review = shops_collection.find_one(
        {
            "_id": shop_id_obj,
            "deleted": False,
            "reviews": {
                "$elemMatch": {
                    "user_id": user_id_obj,
                    "deleted": False
                }
            }
        },
        {"reviews.$": 1}
    )

    if not review or not review.get('reviews'):
        return jsonify({"error": "Review not found"}), 404

    if "photo" not in review['reviews'][0] or review['reviews'][0]["photo"] is None:
        return jsonify({"error": "Photo not found"}), 404

    # Delete photo using arrayFilters
    result = shops_collection.update_one(
        {
            "_id": shop_id_obj,
            "reviews": {
                "$elemMatch": {
                    "user_id": user_id_obj,
                    "deleted": False
                }
            }
        },
        {
            "$set": {"reviews.$[review].photo": None}
        },
        array_filters=[
            {
                "review.user_id": user_id_obj,
                "review.deleted": False
            }
        ]
    )

    if result.modified_count == 0:
        return jsonify({"error": "Failed to delete photo"}), 500

    return jsonify({"message": "Review photo deleted successfully"}), 200
