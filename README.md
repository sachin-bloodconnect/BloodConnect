# BloodConnect

A beginner-friendly Flask + MySQL college project with HTML5/CSS3 only. No JavaScript, Node.js, npm, React, Vue, Angular, Bootstrap or jQuery.

## Run on Windows / VS Code

1. Open the `bloodconnect` folder in VS Code.
2. Create the MySQL database by running `database/schema.sql` in MySQL Workbench or the MySQL command line.
3. Edit `config.py` and enter your local MySQL username/password. Prefer environment variables for any real secret.
4. In the project folder run:

```text
py -m pip install -r requirements.txt
py app.py
```

5. Open:

```text
http://127.0.0.1:5000
```

## Create an admin account

After the database exists, run:

```text
flask --app app create-admin
```

Then follow the prompts. The application stores the password as a hash.

## Notes

- All interactions use normal HTML forms and Flask routes.
- Donor public location is limited to city/area; exact home addresses are not displayed.
- Blood stock is limited to data entered by participating hospitals in the BloodConnect database.
- Emergency requests use database notifications instead of browser push notifications.
- The password reset page shows a demo reset URL on-screen instead of sending an email, so no paid email service is required.
