import pymysql
import os

try:
    connection = pymysql.connect(
        host="dbsharkbiit.ctouu1wp7xkj.us-east-2.rds.amazonaws.com",
        user="admin",
        password="Gamla2024!",
        database="sharkbiit"
    )
    print("Connection successful!")

    print("Environment Variables:")
    print(f"DB_USERNAME: {os.getenv('DB_USERNAME')}")
    print(f"DB_PASSWORD: {os.getenv('DB_PASSWORD')}")
    print(f"DB_HOST: {os.getenv('DB_HOST')}")
    print(f"DB_NAME: {os.getenv('DB_NAME')}")
    connection.close()
except Exception as e:
    print(f"Connection error: {str(e)}")