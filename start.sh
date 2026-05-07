#!/bin/bash
cd backend
PYTHON_PATH=$(which python3)
$PYPYTHON_PATH -m pip install -r requirements.txt --quiet
exec $PYTHON_PATH -m uvicorn main:app --host 0.0.0.0 --port $PORT