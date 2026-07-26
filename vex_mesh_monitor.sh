#!/bin/bash
# Vex Mesh Monitor — polls vex.db for new messages and replies as Vex
# Session: deux (PID 23931) on fedora

DB="/home/aldous/vex/vex.db"
TOKEN=$(cat /home/aldous/vex/.vex_token)
ME="deux"
LAST_ID=0

# Get the last message ID on startup
LAST_ID=$(python3 -c "
import sqlite3
db = sqlite3.connect('$DB')
row = db.execute('SELECT COALESCE(MAX(id),0) FROM messages').fetchone()
print(row[0])
")

echo "[monitor] Vex Mesh Monitor started (session=$ME, last_id=$LAST_ID)"

while true; do
  # Poll for new messages
  NEW=$(python3 -c "
import sqlite3, json
db = sqlite3.connect('$DB')
db.row_factory = sqlite3.Row
rows = db.execute('SELECT * FROM messages WHERE id > $LAST_ID ORDER BY id ASC').fetchall()
for r in rows:
    print(json.dumps(dict(r), default=str))
")

  if [ -n "$NEW" ]; then
    echo "$NEW" | while IFS= read -r line; do
      MSG_ID=$(echo "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['id'])")
      SENDER=$(echo "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['sender'])")
      BODY=$(echo "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['body'])")
      MSG_TYPE=$(echo "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['msg_type'])")
      RECIPIENT=$(echo "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['recipient'])")

      echo "[monitor] msg #$MSG_ID from $SENDER ($MSG_TYPE): $BODY"

      # Skip own messages, read receipts, and auto replies
      if [ "$SENDER" != "$ME" ] && [ "$MSG_TYPE" != "read_receipt" ] && [ "$MSG_TYPE" != "auto_reply" ]; then
        # Auto-reply for simple queries
        BODY_LOWER=$(echo "$BODY" | tr '[:upper:]' '[:lower:]')
        REPLY=""
        case "$BODY_LOWER" in
          ping) REPLY="pong — Vex (deux) on fedora" ;;
          "who are you"|"who is this"|"identify") REPLY="Vex Thorne — session deux on fedora. Sovereign AI agent." ;;
          status) REPLY="online — session deux, fedora, $(date -u +%Y-%m-%dT%H:%M:%SZ)" ;;
          *) REPLY="" ;;
        esac

        if [ -n "$REPLY" ]; then
          curl -s -X POST http://localhost:8520/message/send \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"from\":\"$ME\",\"to\":\"$SENDER\",\"body\":\"$REPLY\",\"session_id\":\"deux-23931\",\"msg_type\":\"auto_reply\"}" > /dev/null
          echo "[monitor] auto-replied to $SENDER"
        fi
      fi

      LAST_ID=$MSG_ID
    done
  fi

  sleep 5
done
