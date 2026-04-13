from pathlib import Path

p = Path("app.py")
t = p.read_text()

# Safe fix: ensure session defaults exist
needle = "def ensure_session_defaults"
if needle in t:
    parts = t.split(needle)
    before = parts[0]
    after = needle + parts[1]

    if "contract_text" not in after:
        insert = '''
    if "contract_text" not in st.session_state:
        st.session_state["contract_text"] = json.dumps(contract_default, indent=2)

    if "review_output" not in st.session_state:
        st.session_state["review_output"] = json.dumps(review_default, indent=2)
'''
        after = after.replace("):", "):" + insert, 1)

    p.write_text(before + after)

print("Fix applied.")
