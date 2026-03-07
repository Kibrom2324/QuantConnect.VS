#!/bash
mkdir -p ~/.lean
echo "{
  \"user-id\": \"$1\",
  \"api-token\": \"$2\"
}" > ~/.lean/credentials
chmod 600 ~/.lean/credentials
echo "Credentials file created at ~/.lean/credentials"
