# Authored By Iota Coders © 2025
import os
from typing import List

import yaml

languages = {}
languages_present = {}


def get_string(lang: str):
    return languages.get(lang) or languages.get("en") or {}


_lang_dir = os.path.join(os.path.dirname(__file__), "langs")
if not os.path.isdir(_lang_dir):
    _lang_dir = r"./strings/langs/"

for filename in os.listdir(_lang_dir):
    if not filename.endswith(".yml"):
        continue
    try:
        if "en" not in languages:
            en_path = os.path.join(_lang_dir, "en.yml")
            languages["en"] = yaml.safe_load(open(en_path, encoding="utf8")) or {}
            languages_present["en"] = languages["en"].get("name", "English")
        language_name = filename[:-4]
        if language_name == "en":
            continue
        path = os.path.join(_lang_dir, filename)
        languages[language_name] = yaml.safe_load(open(path, encoding="utf8")) or {}
        for item in languages["en"]:
            if item not in languages[language_name]:
                languages[language_name][item] = languages["en"][item]
        languages_present[language_name] = languages[language_name].get(
            "name", language_name
        )
    except Exception as e:
        print(f"Language file issue ({filename}): {e} — skipping")