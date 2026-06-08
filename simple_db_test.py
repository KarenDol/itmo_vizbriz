import pymysql

try:
    # Attempt to establish a connection
    connection = pymysql.connect(
        host='localhost',
        port=3307,  # Updated port
        user='root',
        password='new_password',
        database='vizbriz'
    )
    
    # If we got here, the connection was successful
    print("✅ Database connection successful!")
    
    # Test a simple query
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        if tables:
            print(f"Tables in the database:")
            for table in tables:
                print(f"  - {table[0]}")
        else:
            print("No tables found in the database.")
    
    # Close the connection
    connection.close()
    
except Exception as e:
    print(f"❌ Database connection failed: {str(e)}") 