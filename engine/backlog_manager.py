
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict

class BacklogManager:
    def __init__(self, root: Path):
        self.root = root
        self.file = self.root / "backlog" / "di_backlog.json"
        self._ensure_file()

    def _ensure_file(self):
        if not self.file.exists():
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps({
                "artifact": "di_backlog",
                "updated_at": datetime.utcnow().isoformat(),
                "items": []
            }, indent=2))

    def load(self) -> Dict:
        with open(self.file) as f:
            return json.load(f)

    def save(self, data: Dict):
        data["updated_at"] = datetime.utcnow().isoformat()
        with open(self.file, "w") as f:
            json.dump(data, f, indent=2)

    def add_item(self, title: str, project: str, notes: str = "") -> Dict:
        data = self.load()
        items = data["items"]

        new_id = f"BL-{len(items)+1:03d}"

        item = {
            "id": new_id,
            "project": project,
            "created_at": datetime.utcnow().isoformat(),
            "title": title,
            "status": "parking_lot",
            "notes": notes
        }

        items.append(item)
        data["items"] = items
        self.save(data)
        return item

    def list_items(self) -> List[Dict]:
        return self.load().get("items", [])
