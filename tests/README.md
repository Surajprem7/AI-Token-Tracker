# Browser test

Loads the extension in Chrome against a stand-in claude.ai page and takes screenshots.

```bash
cd tests
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 2 -subj "/CN=claude.ai" -addext "subjectAltName=DNS:claude.ai"
python3 mock_claude.py &                 # fake claude.ai on 127.0.0.1:8443
npm install playwright
../build.sh && rm -rf ext && unzip -q ../dist/*-chrome.zip -d ext
node browser-test.js "$PWD/ext"          # screenshots go to tests/out/
```

It checks the sidebar bars, the line under the message box, "messages left" learning,
collapsing, the popup, Hindi and the toolbar badge, and prints any errors.
