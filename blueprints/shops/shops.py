import io
from bson import ObjectId
from flask import request, jsonify, Blueprint, send_file
import utilities.verify as verify
import utilities.auth as auth
from PIL import Image
import math

shops_blueprint = Blueprint('shops', __name__)


@shops_blueprint.route('/', methods=['POST'])
@auth.is_user
def create_shop(*args, **kwargs):
    """Create a new shop"""
    user_id = kwargs.get('user_id')
    is_admin = kwargs.get('is_admin')

    # Get fields from request
    owner_id = user_id
    title = request.args.get('title')
    lat = request.args.get('latitude')
    long = request.args.get('longitude')
    website = request.args.get('website')
    phone = request.args.get('phone')
    street = request.args.get('street')
    city = request.args.get('city')
    category = request.args.get('category')

    # Overwrite owner_id if admin is creating a business for some other user
    if is_admin and request.args.get('owner_id') is not None:
        owner_id = request.args.get('owner_id')

    corrections = []

    # Required fields
    shop = {
        "owner_id": owner_id,
        "title": title,
        "street": street,
        "city": city,
        "reviews": [],
        "photo": None,
        "deleted": False,
    }

    # Verification of required fields
    if not title:
        corrections.append("Title is required")
    elif len(title) < 3 or len(title) > 100:
        corrections.append("Title must be between 3 and 100 characters")

    if not street:
        corrections.append("Street is required")
    elif len(street) < 3 or len(street) > 100:
        corrections.append("Street must be between 3 and 100 characters")

    if not city:
        corrections.append("City is required")
    elif len(city) < 3 or len(city) > 50:
        corrections.append("City must be between 3 and 50 characters")

    # Verification of optional fields if present
    if lat or long:
        location_corrections = verify.check_location(lat, long)
        if location_corrections:
            corrections.extend(location_corrections)
        else:
            # Ensure coordinates are stored as numbers, not strings
            try:
                long_float = float(long)
                lat_float = float(lat)
                shop["location"] = {"type": "Point", "coordinates": [long_float, lat_float]}
            except (ValueError, TypeError):
                corrections.append("Invalid latitude or longitude format")

    if website:
        if len(website) < 3 or len(website) > 200:
            corrections.append("Website must be between 3 and 200 characters")
        else:
            shop["website"] = website

    if phone:
        phone_corrections = verify.check_phone_number(phone)
        if phone_corrections:
            corrections.extend(phone_corrections)
        else:
            shop["phone"] = phone

    if category:
        valid_categories = get_types_of_shop()
        if category not in valid_categories:
            corrections.append(f"Not a valid category. Valid categories: {', '.join(valid_categories)}")
        else:
            shop["categoryName"] = category

    if corrections:
        return jsonify({"corrections": corrections}), 400

    # Push shop to db
    shop_collection = auth.create_collection_connection("Shops")
    result = shop_collection.insert_one(shop)
    shop_id = str(result.inserted_id)

    return jsonify({"shop_id": shop_id}), 201


@shops_blueprint.route('/', methods=['GET'])
def get_shops(*args, **kwargs):
    """Get list of shops with pagination and filters"""
    # Pagination
    per_page = int(request.args.get('per_page', 20))
    page = int(request.args.get('page', 1))

    if page < 1 or per_page < 1:
        return jsonify({"error": "Pagination values cannot be less than 1"}), 400

    offset = (page - 1) * per_page

    # Filters
    filters : dict = {"deleted": False}

    # Add filter for category
    category = request.args.get('category')
    if category:
        filters['categoryName'] = category

    # Add filter for city
    city = request.args.get('city')
    if city:
        filters['city'] = {'$regex': city, '$options': 'i'}

    # Add filter for title search
    search = request.args.get('search')
    if search:
        filters['title'] = {'$regex': search, '$options': 'i'}

    # Add geolocation filter
    latitude = request.args.get('latitude')
    longitude = request.args.get('longitude')
    radius = request.args.get('radius')  # in meters

    use_geolocation = False
    geolocation_coords = None
    geolocation_radius = None

    if latitude and longitude:
        try:
            lat_float = float(latitude)
            long_float = float(longitude)
            radius_float = float(radius) if radius else 5000  # default 5km radius

            # Validate coordinates
            if -90 <= lat_float <= 90 and -180 <= long_float <= 180 and radius_float > 0:
                use_geolocation = True
                geolocation_coords = [long_float, lat_float]
                geolocation_radius = radius_float
            else:
                return jsonify({"error": "Invalid latitude, longitude, or radius values"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "Latitude, longitude, and radius must be valid numbers"}), 400

    # Make request
    shop_collection = auth.create_collection_connection("Shops")

    # For geolocation queries, add location filter using $geoWithin
    if use_geolocation:
        # Add location filter - use $centerSphere for radius in radians
        # Radius in radians = radius in meters / Earth radius in meters
        radius_in_radians = geolocation_radius / 6378100

        filters['location'] = {
            '$geoWithin': {
                '$centerSphere': [geolocation_coords, radius_in_radians]
            }
        }

    filtered_shop_count = shop_collection.count_documents(filters)

    if filtered_shop_count == 0:
        return jsonify({"shops": [], "total": 0, "page": page, "per_page": per_page}), 200

    if filtered_shop_count < offset:
        return jsonify({"error": "Page offset exceeds total results"}), 404

    pipeline = [
        {"$match": filters},
    ]

    # Add distance calculation if using geolocation
    # mongo atlas doesn't support some $geo commands, followed implementation from YouTube
    if use_geolocation:
        pipeline.extend([
            {
                "$addFields": {
                    "distance": {
                        "$let": {
                            "vars": {
                                "lon1": {"$arrayElemAt": ["$location.coordinates", 0]},
                                "lat1": {"$arrayElemAt": ["$location.coordinates", 1]},
                                "lon2": geolocation_coords[0],
                                "lat2": geolocation_coords[1]
                            },
                            "in": {
                                "$multiply": [
                                    6371000,  # Earth radius in meters
                                    {
                                        "$acos": {
                                            "$max": [
                                                -1,
                                                {
                                                    "$min": [
                                                        1,
                                                        {
                                                            "$add": [
                                                                {
                                                                    "$multiply": [
                                                                        {"$sin": {"$degreesToRadians": "$$lat1"}},
                                                                        {"$sin": {"$degreesToRadians": "$$lat2"}}
                                                                    ]
                                                                },
                                                                {
                                                                    "$multiply": [
                                                                        {"$cos": {"$degreesToRadians": "$$lat1"}},
                                                                        {"$cos": {"$degreesToRadians": "$$lat2"}},
                                                                        {"$cos": {
                                                                            "$degreesToRadians": {
                                                                                "$subtract": ["$$lon2", "$$lon1"]}
                                                                        }}
                                                                    ]
                                                                }
                                                            ]
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    }
                                ]
                            }
                        }
                    }
                }
            },
            {"$sort": {"distance": 1}}
        ])

    pipeline.extend([
        {"$skip": offset},
        {"$limit": per_page},
        {
            "$addFields": {
                "avgScore": {
                    "$cond": {
                        "if": {"$gt": [{"$size": "$reviews"}, 0]},
                        "then": {
                            "$avg": {
                                "$map": {
                                    "input": {
                                        "$filter": {
                                            "input": "$reviews",
                                            "as": "review",
                                            "cond": {"$eq": [{"$ifNull": ["$$review.deleted", False]}, False]}
                                        }
                                    },
                                    "as": "review",
                                    "in": "$$review.score"
                                }
                            }
                        },
                        "else": None
                    }
                },
                "reviewCount": {
                    "$size": {
                        "$filter": {
                            "input": "$reviews",
                            "as": "review",
                            "cond": {"$eq": [{"$ifNull": ["$$review.deleted", False]}, False]}
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 1,
                "title": 1,
                "website": 1,
                "phone": 1,
                "street": 1,
                "city": 1,
                "categoryName": 1,
                "location": 1,
                "avgScore": 1,
                "reviewCount": 1,
                "distance": 1  # Include distance if using geolocation
            }
        }
    ])

    shops = list(shop_collection.aggregate(pipeline))

    for shop in shops:
        shop["_id"] = str(shop["_id"])
        # Remove distance field if not using geolocation
        if not use_geolocation and "distance" in shop:
            del shop["distance"]

    total_pages = math.ceil(filtered_shop_count / per_page)

    return jsonify({
        "shops": shops,
        "total": filtered_shop_count,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages
    }), 200


@shops_blueprint.route("/<shop_id>", methods=['GET'])
def get_shop(shop_id: str, *args, **kwargs):
    """Get a single shop by ID"""
    if shop_id is None:
        return jsonify({"error": "shop_id not supplied"}), 400

    try:
        shop_id_obj = ObjectId(shop_id)
    except:
        return jsonify({"error": "Invalid shop_id format"}), 400

    shop_collection = auth.create_collection_connection("Shops")

    # Use aggregation to calculate average score
    pipeline = [
        {"$match": {"_id": shop_id_obj, "deleted": False}},
        {
            "$addFields": {
                "avgScore": {
                    "$cond": {
                        "if": {"$gt": [{"$size": "$reviews"}, 0]},
                        "then": {
                            "$avg": {
                                "$map": {
                                    "input": {
                                        "$filter": {
                                            "input": "$reviews",
                                            "as": "review",
                                            "cond": {"$eq": [{"$ifNull": ["$$review.deleted", False]}, False]}
                                        }
                                    },
                                    "as": "review",
                                    "in": "$$review.score"
                                }
                            }
                        },
                        "else": None
                    }
                },
                "reviewCount": {
                    "$size": {
                        "$filter": {
                            "input": "$reviews",
                            "as": "review",
                            "cond": {"$eq": [{"$ifNull": ["$$review.deleted", False]}, False]}
                        }
                    }
                }
            }
        },
        {"$project": {
            "reviews": 0,
            "photo": 0,
        }}
    ]

    result = list(shop_collection.aggregate(pipeline))

    if not result:
        return jsonify({"error": "Shop not found"}), 404

    shop = result[0]
    shop["_id"] = str(shop["_id"])

    return jsonify(shop), 200


@shops_blueprint.route("/<shop_id>", methods=['PUT'])
@auth.is_user
def update_shop(shop_id: str, *args, **kwargs):
    """Update shop information"""
    user_id = kwargs.get('user_id')
    is_admin = kwargs.get('is_admin')

    try:
        shop_id_obj = ObjectId(shop_id)
    except:
        return jsonify({"error": "Invalid shop_id format"}), 400

    shop_collection = auth.create_collection_connection("Shops")

    # Check if shop exists and user has permission
    shop = shop_collection.find_one({"_id": shop_id_obj, "deleted": False})

    if not shop:
        return jsonify({"error": "Shop not found"}), 404

    if shop.get("owner_id") != user_id and not is_admin:
        return jsonify({"error": "You are not allowed to update this shop"}), 403

    # Get update fields
    updates = {}
    corrections = []

    title = request.args.get('title')
    if title:
        if len(title) < 3 or len(title) > 100:
            corrections.append("Title must be between 3 and 100 characters")
        else:
            updates["title"] = title

    street = request.args.get('street')
    if street:
        if len(street) < 3 or len(street) > 100:
            corrections.append("Street must be between 3 and 100 characters")
        else:
            updates["street"] = street

    city = request.args.get('city')
    if city:
        if len(city) < 3 or len(city) > 50:
            corrections.append("City must be between 3 and 50 characters")
        else:
            updates["city"] = city

    lat = request.args.get('latitude')
    long = request.args.get('longitude')
    if lat or long:
        location_corrections = verify.check_location(lat, long)
        if location_corrections:
            corrections.extend(location_corrections)
        else:
            # Ensure coordinates are stored as numbers, not strings
            try:
                long_float = float(long)
                lat_float = float(lat)
                updates["location"] = {"type": "Point", "coordinates": [long_float, lat_float]}
            except (ValueError, TypeError):
                corrections.append("Invalid latitude or longitude format")

    website = request.args.get('website')
    if website:
        if len(website) < 3 or len(website) > 200:
            corrections.append("Website must be between 3 and 200 characters")
        else:
            updates["website"] = website

    phone = request.args.get('phone')
    if phone:
        phone_corrections = verify.check_phone_number(phone)
        if phone_corrections:
            corrections.extend(phone_corrections)
        else:
            updates["phone"] = phone

    category = request.args.get('category')
    if category:
        valid_categories = get_types_of_shop()
        if category not in valid_categories:
            corrections.append(f"Not a valid category")
        else:
            updates["categoryName"] = category

    if corrections:
        return jsonify({"corrections": corrections}), 400

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    # Update shop
    result = shop_collection.update_one(
        {"_id": shop_id_obj},
        {"$set": updates}
    )

    if result.modified_count == 0:
        return jsonify({"message": "No changes made"}), 200

    return jsonify({"message": "Shop updated successfully"}), 200


@shops_blueprint.route("/<shop_id>/photo", methods=['PUT'])
@auth.is_user
def update_photo(shop_id: str, *args, **kwargs):
    """Upload or update shop photo"""
    photo = request.files.get('photo')

    if not photo:
        return jsonify({"error": "No photo provided"}), 400

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

    shop_collection = auth.create_collection_connection("Shops")

    shop = shop_collection.find_one({"_id": ObjectId(shop_id), "deleted": False})

    if shop is None:
        return jsonify({"error": "Shop not found"}), 404

    if shop.get("owner_id") != kwargs["user_id"] and not kwargs["is_admin"]:
        return jsonify({"error": "You are not allowed to update this shop"}), 403

    # Store binary data
    shop_collection.update_one(
        {"_id": ObjectId(shop_id)},
        {"$set": {"photo": photo_bytes}}
    )

    return jsonify({"message": "Photo updated successfully"}), 200


@shops_blueprint.route("/<shop_id>/photo", methods=['GET'])
def get_photo(shop_id: str):
    """Get shop photo"""
    shop_collection = auth.create_collection_connection("Shops")

    shop = shop_collection.find_one({"_id": ObjectId(shop_id), "deleted": False})

    if shop is None:
        return jsonify({"error": "Shop not found"}), 404

    if "photo" not in shop or shop["photo"] is None:
        return jsonify({"error": "Photo not found"}), 404

    return send_file(
        io.BytesIO(shop["photo"]),
        mimetype='image/jpeg'
    )


@shops_blueprint.route("/<shop_id>/photo", methods=['DELETE'])
@auth.is_user
def delete_photo(shop_id: str, *args, **kwargs):
    """Delete shop photo"""
    shop_collection = auth.create_collection_connection("Shops")

    shop = shop_collection.find_one({"_id": ObjectId(shop_id), "deleted": False})

    if shop is None:
        return jsonify({"error": "Shop not found"}), 404

    if shop.get("owner_id") != kwargs["user_id"] and not kwargs["is_admin"]:
        return jsonify({"error": "You are not allowed to update this shop"}), 403

    if "photo" not in shop or shop["photo"] is None:
        return jsonify({"error": "No photo to delete"}), 404

    # Remove the photo field
    shop_collection.update_one(
        {"_id": ObjectId(shop_id)},
        {"$unset": {"photo": ""}}
    )

    return jsonify({"message": "Photo deleted successfully"}), 200


@shops_blueprint.route("/<shop_id>/delete", methods=['DELETE'])
@auth.is_admin
def delete_shop(shop_id: str, *args, **kwargs):
    """Permanently delete a shop (admin only)"""
    title = request.args.get('title')

    if not title:
        return jsonify({"error": "Title confirmation required"}), 400

    shop_collection = auth.create_collection_connection("Shops")

    shop = shop_collection.find_one({"_id": ObjectId(shop_id)})

    if shop is None:
        return jsonify({"error": "Shop not found"}), 404

    if shop["title"] != title:
        return jsonify({"error": "Title doesn't match, shop not deleted"}), 400

    result = shop_collection.delete_one({"_id": ObjectId(shop_id)})

    if result.deleted_count == 0:
        return jsonify({"error": "Shop not deleted"}), 500

    return jsonify({"message": f"Shop '{title}' deleted successfully"}), 200


@shops_blueprint.route("/<shop_id>/deactivate", methods=['POST'])
@auth.is_user
def deactivate_shop(shop_id: str, *args, **kwargs):
    """Deactivate a shop (soft delete)"""
    user_id = kwargs["user_id"]
    is_admin = kwargs.get("is_admin", False)

    shop_collection = auth.create_collection_connection("Shops")

    shop = shop_collection.find_one({"_id": ObjectId(shop_id), "deleted": False})

    if shop is None:
        return jsonify({"error": "Shop not found"}), 404

    if shop["owner_id"] != user_id and not is_admin:
        return jsonify({"error": "You are not allowed to deactivate this shop"}), 403

    result = shop_collection.update_one(
        {"_id": ObjectId(shop_id)},
        {"$set": {"deleted": True}}
    )

    if result.modified_count == 0:
        return jsonify({"error": "Shop not deactivated"}), 500

    return jsonify({"message": f"Shop '{shop['title']}' deactivated successfully"}), 200


@shops_blueprint.route("/<shop_id>/reactivate", methods=['POST'])
@auth.is_admin
def reactivate_shop(shop_id: str, *args, **kwargs):
    """Reactivate a deactivated shop (admin only)"""
    new_owner_id = request.args.get('new_owner_id')

    shop_collection = auth.create_collection_connection("Shops")

    shop = shop_collection.find_one({"_id": ObjectId(shop_id)})

    if shop is None:
        return jsonify({"error": "Shop not found"}), 404

    if not shop.get("deleted", False):
        return jsonify({"error": "Shop is not currently deactivated"}), 400

    changed_values : dict = {"deleted": False}

    if new_owner_id is not None:
        # Verify new owner exists
        user_collection = auth.create_collection_connection("Users")
        new_owner = user_collection.find_one({"_id": ObjectId(new_owner_id), "deleted": False})
        if not new_owner:
            return jsonify({"error": "New owner not found"}), 404
        changed_values["owner_id"] = new_owner_id

    result = shop_collection.update_one(
        {"_id": ObjectId(shop_id)},
        {"$set": changed_values}
    )

    if result.modified_count == 0:
        return jsonify({"error": "Shop not reactivated"}), 500

    return jsonify({"message": f"Shop '{shop['title']}' reactivated successfully"}), 200


@shops_blueprint.route('/get_types', methods=['GET'])
def get_types():
    """Get list of all shop categories"""
    return jsonify({"categories": get_types_of_shop()}), 200


def get_types_of_shop():
    """Helper function to get unique shop categories"""
    shops_collection = auth.create_collection_connection("Shops")

    pipeline = [
        {"$match": {"deleted": False, "categoryName": {"$exists": True, "$ne": None}}},
        {
            '$group': {
                '_id': '$categoryName',
                'count': {'$sum': 1}
            }
        },
        {
            '$sort': {'count': -1}
        }
    ]

    results = shops_collection.aggregate(pipeline)
    categories = [doc["_id"] for doc in results if doc["_id"]]

    return categories
