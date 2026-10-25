def find(db, name):
    return db.execute(f"SELECT id FROM users WHERE name = '{name}'")
