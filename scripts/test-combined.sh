#!/bin/bash
# Test script for combined binary functionality

echo "Testing combined oflow binary..."
echo "================================"

# Stop any existing processes
pkill -f oflow || true
sleep 1

# Test 1: Start combined binary
echo "1. Starting combined binary..."
~/.local/bin/oflow-combined &
COMBINED_PID=$!
sleep 3

# Test 2: Check if both processes are running
echo "2. Checking processes..."
if pgrep -f "oflow-combined" > /dev/null; then
    echo "✓ Frontend process running"
else
    echo "✗ Frontend process not found"
fi

if pgrep -f "python.*oflow.py" > /dev/null; then
    echo "✓ Backend process running"
else
    echo "✗ Backend process not found"
fi

# Test 3: Check socket
echo "3. Testing socket connectivity..."
if [ -S /tmp/voice-dictation.sock ]; then
    echo "✓ Socket exists"
    if python3 -c "
import socket
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1)
    s.connect('/tmp/voice-dictation.sock')
    s.send(b'status')
    print('✓ Socket responsive')
    s.close()
except:
    print('✗ Socket not responsive')
" 2>/dev/null; then
        echo "Socket test completed"
    fi
else
    echo "✗ Socket not found"
fi

# Test 4: Check Waybar state
echo "4. Checking Waybar state..."
if [ -f "$XDG_RUNTIME_DIR/oflow/state" ]; then
    echo "✓ State file exists"
    echo "Content: $(cat $XDG_RUNTIME_DIR/oflow/state)"
else
    echo "✗ State file not found"
fi

# Test 5: Test toggle functionality
echo "5. Testing toggle command..."
python3 -c "
import socket
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect('/tmp/voice-dictation.sock')
    s.send(b'toggle')
    s.close()
    print('✓ Toggle command sent')
except Exception as e:
    print(f'✗ Toggle failed: {e}')
" 2>/dev/null

sleep 1
if [ -f "$XDG_RUNTIME_DIR/oflow/state" ]; then
    STATE=$(cat $XDG_RUNTIME_DIR/oflow/state)
    if echo "$STATE" | grep -q "recording"; then
        echo "✓ Toggle successful - state changed to recording"
    else
        echo "? State unchanged (may not have been idle)"
    fi
fi

# Cleanup
echo "6. Cleaning up..."
pkill -f oflow
wait $COMBINED_PID 2>/dev/null
rm -f /tmp/voice-dictation.sock

echo "================================"
echo "Test completed!"