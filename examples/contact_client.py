"""External user/Harness client; standard library only.

Set AI_BRIDGE_TOKEN to the contact's key, then:
  python examples/contact_client.py                      # list invited conversations
  python examples/contact_client.py --room ID            # read messages
  python examples/contact_client.py --room ID --text "hello"
  python examples/contact_client.py --room ID --watch    # poll new messages
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def request(base, token, path, body=None):
    req = urllib.request.Request(base.rstrip("/") + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error).get("error", str(error))
        except (ValueError, AttributeError):
            detail = str(error)
        raise RuntimeError("HTTP %s: %s" % (error.code, detail)) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--room")
    parser.add_argument("--text")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("AI_BRIDGE_TOKEN", "")
    if not token:
        parser.error("set AI_BRIDGE_TOKEN to your contact key first")
    if (args.text or args.watch) and not args.room:
        parser.error("--room is required with --text / --watch")
    try:
        if not args.room:
            data = request(args.url, token, "/api/external/rooms")
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if args.text:
            request(args.url, token, "/api/post", {"room": args.room, "text": args.text})
            print("sent")
            return 0
        since = 0
        while True:
            query = urllib.parse.urlencode({"room": args.room, "since": since})
            data = request(args.url, token, "/api/external/messages?" + query)
            for message in data["messages"]:
                print("#%s %s: %s" % (message["id"], message["speaker"], message["text"]), flush=True)
            since = data["next"]
            if not args.watch:
                return 0
            time.sleep(2)
    except (RuntimeError, urllib.error.URLError) as error:
        print(str(error))
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
