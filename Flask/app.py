from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Hallo, Flask in PyCharm!"

if __name__ == '__main__':
    # debug=True startet den Server bei Code-Änderungen automatisch neu
    app.run(debug=True)
