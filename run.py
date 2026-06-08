# run.py

from flask_app import create_app
# Create an instance of the app by calling create_app()
app = create_app()

if __name__ == '__main__':
    # Run the Flask app
    app.run(host='0.0.0.0', port=7000, debug=True)    
