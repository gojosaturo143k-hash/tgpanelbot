"""Registers every Web Panel API blueprint on the Flask app."""


def register_blueprints(app):
    from routes.chats import bp as chats_bp
    from routes.dashboard import bp as dashboard_bp
    from routes.dms import bp as dms_bp
    from routes.groups import bp as groups_bp
    from routes.status import bp as status_bp

    for blueprint in (groups_bp, dms_bp, dashboard_bp, status_bp, chats_bp):
        app.register_blueprint(blueprint)
