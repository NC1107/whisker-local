# Privacy

Litterbox Local doesn't send me anything.
The app only talks to the server you set up and sign in to, and everything it shows, cat names, photos, weights, visits etc, is stored on that server, on your own network.
There are no accounts, analytics, ads or tracking in the app.

## Push notifications

Push is off by default.
If you turn it on, your server sends the alert text and your phone's push token to a small relay so it can be passed to apple.
The relay is open source at https://github.com/NC1107/litterbox-local-relay.
It keeps the phone token tied to a hashed key for your server so it knows where to send, it doesn't store the notification text, and it doesn't log tokens or text.
Turning push off on your server stops all of that.

## Your server

Whoever runs the server controls the data on it.
If that's you, you can export it or delete it whenever, nothing is copied anywhere else.

## Questions

Open an issue at https://github.com/NC1107/whisker-local/issues.
