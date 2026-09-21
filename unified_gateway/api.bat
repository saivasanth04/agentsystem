@echo off
setlocal enabledelayedexpansion
title Unified Multi-Provider LLM Gateway

echo ===============================================================================
echo                ONE UNIFIED MULTI-PROVIDER LLM GATEWAY
echo ===============================================================================
echo [1/3] Checking environment and generating unified credentials...

:: Ensure unified key is created and display it
python -c "from gateway.config import get_or_create_unified_key; key = get_or_create_unified_key(); print('\n' + '='*79 + '\n  YOUR UNIFIED API KEY:\n  ' + key + '\n' + '='*79 + '\n')"

echo -------------------------------------------------------------------------------
echo [2/3] SAMPLE USAGE (Python OpenAI SDK with model='auto'):
echo -------------------------------------------------------------------------------
echo.
echo   from openai import OpenAI
echo.
echo   # Use the key printed above
echo   client = OpenAI(
echo       base_url="http://127.0.0.1:8000/v1",
echo       api_key="<YOUR_UNIFIED_KEY>"
echo   )
echo.
echo   response = client.chat.completions.create(
echo       model="auto",  # Dynamic auto-routing across 32 providers
echo       messages=[
echo           {"role": "user", "content": "Hello! Explain quantum computing simply."}
echo       ]
echo   )
echo   print(response.choices[0].message.content)
echo.
echo -------------------------------------------------------------------------------
echo [cURL Example]:
echo   curl http://127.0.0.1:8000/v1/chat/completions ^
echo     -H "Authorization: Bearer <YOUR_UNIFIED_KEY>" ^
echo     -H "Content-Type: application/json" ^
echo     -d "{\"model\": \"auto\", \"messages\": [{\"role\": \"user\", \"content\": \"Hi!\"}]}"
echo -------------------------------------------------------------------------------
echo.
echo [3/3] Starting Unified Gateway Server on http://127.0.0.1:8000 ...
echo Press Ctrl+C to stop the server at any time.
echo.

python -m gateway.main --host 127.0.0.1 --port 8000

pause
