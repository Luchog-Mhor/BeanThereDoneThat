from flask import Flask, jsonify, make_response, request

from blueprints.shops.reviews import reviews_blueprint
from blueprints.shops.shops import shops_blueprint
from blueprints.users.users import users_blueprint
from utilities.auth import is_user
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

api_prefix = "/api/1.0"
app.register_blueprint(users_blueprint, url_prefix=f'{api_prefix}/users')
app.register_blueprint(shops_blueprint, url_prefix=f'{api_prefix}/shops')
app.register_blueprint(reviews_blueprint, url_prefix=f'{api_prefix}/reviews')


@app.route("/", methods=["GET"])
def index():
    return make_response(jsonify({"message": "Welcome to the API", "version": "1.0"}), 200)


@app.route("/self", methods=["GET"])
@is_user
def self(*args, **kwargs):
    """Get current user information from JWT"""
    user_id = kwargs.get('user_id')
    name = kwargs.get('name')
    email = kwargs.get('user_email')
    is_admin = kwargs.get('is_admin')
    is_verified = kwargs.get('is_verified')

    return jsonify({
        "user_id": user_id,
        "name": name,
        "email": email,
        "is_admin": is_admin,
        "is_verified": is_verified,
        "message": "Successfully authenticated"
    }), 200


@app.route("/help", methods=["GET"])
def help(*args, **kwargs):
    """API documentation endpoint"""
    category = request.args.get('category')

    help_data = {
        "api_version": "1.0",
        "base_url": "/api/1.0",
        "categories": ["users", "shops", "reviews"],
        "root_endpoints": {
            "/": "Welcome message",
            "/self": "Get current authenticated user info (requires auth)",
            "/help": "This help endpoint"
        }
    }

    if category == "users":
        help_data["endpoints"] = {
            "POST /api/1.0/users/create": "Create a new user account",
            "POST /api/1.0/users/login": "Login user (returns JWT token)",
            "POST /api/1.0/users/logout": "Logout user (requires auth)",
            "GET /api/1.0/users/self": "Get self (requires auth)",
            "GET /api/1.0/users/<user_id>": "Get user by ID (admin only)",
            "GET /api/1.0/users/": "Get list of users (admin only)",
            "PUT /api/1.0/users/update": "Update user information (requires auth)",
            "POST /api/1.0/users/deactivate": "Deactivate user account (requires auth)",
            "POST /api/1.0/users/recover": "Recover deactivated account (admin only)",
            "DELETE /api/1.0/users/delete": "Permanently delete user (admin only)",
            "DELETE /api/1.0/users/revoke_sessions": "Revoke user sessions (requires auth)",
            "PUT /api/1.0/users/<user_id>": "Set admin status (admin only)"
        }
    elif category == "shops":
        help_data["endpoints"] = {
            "POST /api/1.0/shops/": "Create a new shop (requires auth)",
            "GET /api/1.0/shops/": "Get list of shops with filters",
            "GET /api/1.0/shops/<shop_id>": "Get shop by ID",
            "PUT /api/1.0/shops/<shop_id>": "Update shop information (requires auth)",
            "PUT /api/1.0/shops/<shop_id>/photo": "Upload/update shop photo (requires auth)",
            "GET /api/1.0/shops/<shop_id>/photo": "Get shop photo",
            "DELETE /api/1.0/shops/<shop_id>/photo": "Delete shop photo (requires auth)",
            "POST /api/1.0/shops/<shop_id>/deactivate": "Deactivate shop (requires auth)",
            "POST /api/1.0/shops/<shop_id>/reactivate": "Reactivate shop (admin only)",
            "DELETE /api/1.0/shops/<shop_id>/delete": "Permanently delete shop (admin only)",
            "GET /api/1.0/shops/get_types": "Get list of shop categories"
        }
    elif category == "reviews":
        help_data["endpoints"] = {
            "POST /api/1.0/reviews/": "Create a new review (requires auth)",
            "PUT /api/1.0/reviews/": "Update existing review (requires auth)",
            "GET /api/1.0/reviews/": "Get reviews for a shop",
            "DELETE /api/1.0/reviews/": "Delete a review (requires auth)",
            "POST /api/1.0/reviews/like": "Like a review (requires auth)",
            "DELETE /api/1.0/reviews/like": "Remove like from review (requires auth)",
            "PUT /api/1.0/reviews/photo": "Upload/update review photo (requires auth)",
            "GET /api/1.0/reviews/photo": "Get review photo",
            "DELETE /api/1.0/reviews/photo": "Delete review photo (requires auth)"
        }
    else:
        help_data["message"] = "Specify a category parameter to get detailed endpoint information"
        help_data["example"] = "/help?category=users"

    return jsonify(help_data), 200


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.run(debug=True)
