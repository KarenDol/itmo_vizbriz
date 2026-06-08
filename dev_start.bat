@echo off
echo Starting DEVELOPMENT environment...

echo Setting environment variables...
set FLASK_ENV=development
set DEV_DB_HOST=localhost
set DEV_DB_USER=sharkbiit_user
set DEV_DB_PASSWORD=sharkbiit_password
set DEV_DB_NAME=sharkbiit_db

echo Starting Docker containers...
docker-compose up -d dev_db

echo Activating virtual environment...
call D:\DevEnvironment\venvs\sharkbiit_app\Scripts\activate

echo Starting Flask application...
cd D:\DevEnvironment\projects\SBN
flask run

pause