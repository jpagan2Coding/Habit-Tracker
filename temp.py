from werkzeug.security import generate_password_hash


password = "test123@"

print(generate_password_hash(password))
