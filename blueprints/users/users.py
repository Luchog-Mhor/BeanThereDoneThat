import bson
from bson import ObjectId
from flask import request, make_response, jsonify, Blueprint
from pymongo.collection import Collection
from datetime import datetime
from dateutil import tz
import utilities.verify as verify
import utilities.auth as auth

users_blueprint = Blueprint('users', __name__)


@users_blueprint.route('/create', methods=['POST'])
def create_user(*args, **kwargs):
    """Create a new user account"""
    _request_args = request.args

    if not _request_args:
        return make_response(jsonify({'error': 'No data provided'}), 400)

    # Verify required fields exist
    required_fields = ['password', 'dob_year', 'dob_month', 'dob_day', 'name', 'email']
    for field in required_fields:
        if field not in _request_args:
            return make_response(jsonify({'error': f'Missing required field: {field}'}), 400)

    # Verify arguments
    corrections = {}

    password_corrections = verify.check_password(_request_args['password'])
    if password_corrections:
        corrections['password'] = password_corrections

    dob_corrections = verify.check_dob(_request_args['dob_year'], _request_args['dob_month'], _request_args['dob_day'])
    if dob_corrections:
        corrections['dob'] = dob_corrections

    name_corrections = verify.check_name(_request_args['name'].split(" "))
    if name_corrections:
        corrections['name'] = name_corrections

    if corrections:
        return make_response(jsonify(corrections), 400)

    # Create user
    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    # Check if email already exists
    existing_user = user_collection.find_one({"email": _request_args['email']})
    if existing_user:
        return make_response(jsonify({'error': 'Email already registered'}), 409)

    # Hash password
    hashed_password = auth.generate_password_hash(_request_args['password'])

    # Create DOB datetime
    dob = datetime(
        int(_request_args['dob_year']),
        int(_request_args['dob_month']),
        int(_request_args['dob_day']),
        tzinfo=tz.tzutc()
    )

    # Create user document
    user_doc = {
        'name': _request_args['name'],
        'email': _request_args['email'],
        'password': hashed_password,
        'dob': dob,
        'verified': False,
        'admin': False,
        'deleted': False,
        'sessions': []
    }

    result = user_collection.insert_one(user_doc)
    user_id = str(result.inserted_id)

    # Create session token
    token = auth.create_token(user_id)

    if not token:
        return make_response(jsonify({'error': 'Error creating session'}), 500)

    return jsonify({'user_id': user_id, 'token': token}), 201

@users_blueprint.route('/self', methods=['GET'])
@auth.is_user
def get_self(*args, **kwargs):

    user_id = kwargs.get('user_id')

    # Validate ObjectId format
    try:
        user_id = ObjectId(user_id)
    except (TypeError, bson.errors.InvalidId):
        return make_response(jsonify({'error': 'Invalid user_id format'}), 400)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    # Get user but exclude password field
    user = user_collection.find_one({"_id": user_id, "deleted": False}, {"password": 0, "sessions": 0})

    if user is None:
        return make_response(jsonify({'error': 'User not found'}), 404)

    # Convert ObjectId to string for JSON serialization
    user["_id"] = str(user["_id"])

    # Convert datetime to ISO format if present
    if "dob" in user:
        user["dob"] = user["dob"].isoformat()

    return jsonify({"user" : user}), 200

@users_blueprint.route('/<search_user_id>', methods=['GET'])
@auth.is_admin
def get_user(search_user_id, *args, **kwargs):
    """Get a single user by ID"""
    if search_user_id is None:
        return make_response(jsonify({'error': 'user_id not given'}), 400)

    # Validate ObjectId format
    try:
        search_user_id = ObjectId(search_user_id)
    except (TypeError, bson.errors.InvalidId):
        return make_response(jsonify({'error': 'Invalid user_id format'}), 400)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    # Get user but exclude password field
    user = user_collection.find_one({"_id": search_user_id}, {"password": 0, "sessions": 0})

    if user is None:
        return make_response(jsonify({'error': 'User not found'}), 404)

    # Convert ObjectId to string for JSON serialization
    user["_id"] = str(user["_id"])

    # Convert datetime to ISO format if present
    if "dob" in user:
        user["dob"] = user["dob"].isoformat()

    return jsonify({"user" : user}), 200


@users_blueprint.route('/', methods=['GET'])
@auth.is_admin
def get_users(*args, **kwargs):
    """Get list of users with optional filters (admin only)"""
    # Get arguments to use in filter
    users_filter = {}
    name = request.args.get('name', None)
    email = request.args.get('email', None)
    dob_partial = request.args.get('year', None), request.args.get('month', None), request.args.get('day', None)
    dob_operator = request.args.get('dob_operator', 'eq')
    deleted = request.args.get('is_deleted', None)
    verified = request.args.get('is_verified', None)
    admin = request.args.get('is_admin', None)

    # Use regex for contains behavior
    if name is not None:
        users_filter['name'] = {'$regex': name, '$options': 'i'}

    if email is not None:
        users_filter['email'] = {'$regex': email, '$options': 'i'}

    # Handle boolean filters
    if deleted is not None:
        users_filter['deleted'] = deleted.lower() == 'true'

    if verified is not None:
        users_filter['verified'] = verified.lower() == 'true'

    if admin is not None:
        users_filter['admin'] = admin.lower() == 'true'

    # Handle DOB filtering
    dob_count = len([True for time_step in dob_partial if time_step is not None])

    if dob_count in (1, 2):
        return make_response(jsonify({'error': 'All fields of year, month and day must be passed'}), 400)

    if dob_count == 3:
        try:
            year, month, day = int(dob_partial[0]), int(dob_partial[1]), int(dob_partial[2])
        except ValueError:
            return make_response(jsonify({'error': 'Invalid date values'}), 400)

        if dob_operator not in ("eq", "ne", "lt", "lte", "gt", "gte"):
            return make_response(jsonify({'error': 'Invalid operator'}), 400)

        if not verify.check_date(year, month, day):
            return make_response(jsonify({'error': 'Invalid date'}), 400)

        dob = datetime(year, month, day, tzinfo=tz.tzutc())
        users_filter['dob'] = {f"${dob_operator}": dob}

    # Get pagination parameters
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))

    # Make request for users
    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    # Exclude password and sessions field from results
    users_cursor = user_collection.find(users_filter, {"password": 0, "sessions": 0}).skip(offset).limit(limit)

    users = []
    for user in users_cursor:
        user["_id"] = str(user["_id"])
        if "dob" in user:
            user["dob"] = user["dob"].isoformat()
        users.append(user)

    return make_response(jsonify({'users': users, 'count': len(users)}), 200)


@users_blueprint.route('/login', methods=['POST'])
def login(*args, **kwargs):
    """User login endpoint"""
    if not request.authorization:
        return make_response(jsonify({'error': 'Authorization required'}), 401)

    user_email = request.authorization.get('username', None)
    password = request.authorization.get('password', None)

    if not user_email:
        return make_response(jsonify({'error': 'Email not given'}), 401)

    if not password:
        return make_response(jsonify({'error': 'Password not given'}), 401)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    user = user_collection.find_one({"email": user_email}, {"_id": 1, "password": 1, "deleted": 1, "verified": 1})

    if user is None:
        return make_response(jsonify({'error': 'User not found'}), 404)

    if user.get('deleted', False):
        return make_response(jsonify({'error': 'Account deactivated'}), 403)

    # if not user.get('verified', False):
    #     return make_response(jsonify({'error': 'Account not verified'}), 403)

    # Use proper password verification
    if not auth.verify_password(password, user['password']):
        return make_response(jsonify({'error': 'Invalid password'}), 401)

    token = auth.create_token(str(user["_id"]))

    if not token:
        return make_response(jsonify({'error': 'Error making token'}), 500)

    return jsonify({'token': token}), 200


@users_blueprint.route('/logout', methods=['POST'])
@auth.is_user
def logout(*args, **kwargs):
    """Logout user by removing session"""
    session_id = request.args.get('session_id')
    user_id = kwargs.get('user_id')

    if session_id is None:
        session_id = kwargs.get('session_id')

    if not session_id:
        return make_response(jsonify({'error': 'Session ID required'}), 400)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    result = user_collection.update_one(
        {'_id': ObjectId(user_id)},
        {'$pull': {'sessions': {'session_id': session_id}}}
    )

    if result.modified_count == 0:
        return make_response(jsonify({'error': 'Session not found'}), 404)

    return jsonify({'message': 'Logged out successfully'}), 200


@users_blueprint.route('/update', methods=['PUT'])
@auth.is_user
def update(*args, **kwargs):
    """Update user information"""
    user_id = kwargs.get('user_id')

    if not request.is_json:
        return make_response(jsonify({'error': 'JSON data required'}), 400)

    update_data = request.get_json()
    allowed_fields = ['name', 'email']

    updates = {}
    for field in allowed_fields:
        if field in update_data:
            # Validate name if provided
            if field == 'name':
                name_corrections = verify.check_name(update_data[field].split(" "))
                if name_corrections:
                    return make_response(jsonify({'name': name_corrections}), 400)
            updates[field] = update_data[field]

    if not updates:
        return make_response(jsonify({'error': 'No valid fields to update'}), 400)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    # Check if email already exists (if updating email)
    if 'email' in updates:
        existing_user = user_collection.find_one({"email": updates['email'], "_id": {"$ne": ObjectId(user_id)}})
        if existing_user:
            return make_response(jsonify({'error': 'Email already in use'}), 409)

    result = user_collection.update_one(
        {'_id': ObjectId(user_id)},
        {'$set': updates}
    )

    if result.matched_count == 0:
        return make_response(jsonify({'error': 'User not found'}), 404)

    return jsonify({'message': 'User updated successfully'}), 200


@users_blueprint.route('/deactivate', methods=['POST'])
@auth.is_user
def deactivate(*args, **kwargs):
    """Deactivate user account"""
    user_id = kwargs.get('user_id')

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")
    shops_collection: Collection = auth.create_collection_connection(collection_name="Shops")

    result = user_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"deleted": True, "sessions": []}}
    )

    if result.matched_count == 0:
        return make_response(jsonify({'error': 'User not found'}), 404)

    # Deactivate all shops owned by this user
    shops_collection.update_many(
        {"owner_id": user_id, "deleted": False},
        {"$set": {"deleted": True}}
    )

    return jsonify({'message': 'User has been deactivated'}), 200


@users_blueprint.route('/recover', methods=['POST'])
@auth.is_admin
def recover(*args, **kwargs):
    """Recover a deactivated user account (admin only)"""
    user_id = request.args.get('user_id')

    if not user_id:
        return make_response(jsonify({'error': 'user_id required'}), 400)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")
    shops_collection: Collection = auth.create_collection_connection(collection_name="Shops")

    result = user_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"deleted": False}}
    )

    if result.matched_count == 0:
        return make_response(jsonify({'error': 'User not found'}), 404)

    # Reactivate all shops owned by this user
    shops_collection.update_many(
        {"owner_id": user_id, "deleted": True},
        {"$set": {"deleted": False}}
    )

    return jsonify({'message': 'User has been reactivated'}), 200


@users_blueprint.route('/delete', methods=['DELETE'])
@auth.is_admin
def delete(*args, **kwargs):
    """Permanently delete a user account (admin only)"""
    user_id = request.args.get('user_id')

    if not user_id:
        return make_response(jsonify({'error': 'user_id required'}), 400)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")
    shops_collection: Collection = auth.create_collection_connection(collection_name="Shops")

    result = user_collection.delete_one({"_id": ObjectId(user_id)})

    if result.deleted_count == 0:
        return make_response(jsonify({'error': 'User not found'}), 404)

    # Deactivate shops owned by this user and clear owner field
    shops_collection.update_many(
        {"owner_id": user_id},
        {"$set": {"deleted": True, "owner_id": None}}
    )

    return jsonify({'message': 'User has been deleted'}), 200


@users_blueprint.route('/revoke_sessions', methods=['DELETE'])
@auth.is_user
def revoke_sessions(*args, **kwargs):
    """Revoke user sessions"""
    user_id = kwargs.get('user_id')
    session_id = request.args.get('session_id', None)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    if session_id is None:
        # Revoke all sessions
        result = user_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"sessions": []}}
        )
    else:
        # Revoke specific session
        result = user_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$pull': {'sessions': {'session_id': session_id}}}
        )

    if result.matched_count == 0:
        return make_response(jsonify({'error': 'User not found'}), 404)

    if result.modified_count == 0:
        return make_response(jsonify({'error': 'No sessions deleted'}), 404)

    return jsonify({'message': 'Session(s) have been revoked'}), 200


@users_blueprint.route("/<_user_id>/set_admin", methods=['PUT'])
@auth.is_admin
def set_admin(_user_id, *args, **kwargs):
    """Set admin status for a user (admin only)"""
    this_user_id = kwargs.get('user_id')
    admin_value_str = request.args.get('admin_value', 'false').lower()

    # Convert string to boolean
    admin_value = admin_value_str == 'true'

    if _user_id == this_user_id:
        return make_response(jsonify({'error': 'You can\'t change the status of your own User account'}), 403)

    user_collection: Collection = auth.create_collection_connection(collection_name="Users")

    # Check if user exists
    user = user_collection.find_one({"_id": ObjectId(_user_id), "deleted": False})

    if not user:
        return make_response(jsonify({'error': 'User does not exist'}), 404)

    # Set user admin value to the argument passed in
    result = user_collection.update_one({"_id": ObjectId(_user_id)}, {"$set": {"admin": admin_value}})

    if result.modified_count == 0:
        return make_response(jsonify({'error': 'No changes made to the User'}), 404)

    return jsonify({'message': 'User has been changed successfully'}), 200
