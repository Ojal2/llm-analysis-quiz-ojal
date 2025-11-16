import requests

resp = requests.post(
    "http://127.0.0.1:8000/quiz",
    json={
        "email": "24f2007000@ds.study.iitm.ac.in",
        "secret": "ojal-llm-project-2",
        "url": "https://tds-llm-analysis.s-anand.net/demo"
    }
)

print("Status:", resp.status_code)
print("Raw text:", resp.text)
