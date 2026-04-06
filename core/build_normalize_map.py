"""Build normalize.json from skills_master.json.

Usage:
    python core/build_normalize_map.py

This generates core/normalise.json which maps all skill aliases
to their canonical form. Never edit normalise.json manually.
"""

import json
import os


def normalize_text(s: str) -> str:
    """
    Normalize text for matching:
    - lowercase
    - strip whitespace
    - remove dots (react.js → reactjs)
    - replace hyphens with spaces (scikit-learn → scikit learn)
    - collapse multiple spaces
    """
    if not s:
        return ""
    s = s.lower().strip()
    s = s.replace(".", "")  # react.js → reactjs
    s = s.replace("-", " ")  # scikit-learn → scikit learn
    s = " ".join(s.split())  # collapse multiple spaces
    return s


def build_normalize_map():
    """Read skills_master.json and generate normalise.json."""
    core_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Read skills master
    master_path = os.path.join(core_dir, "skills_master.json")
    with open(master_path, "r", encoding="utf-8") as f:
        master = json.load(f)
    
    # Build alias → canonical mapping
    normalize = {}
    
    for canonical, data in master.items():
        aliases = data.get("aliases", [])
        for alias in aliases:
            # Normalize the alias for matching
            normalized_alias = normalize_text(alias)
            if normalized_alias:
                normalize[normalized_alias] = canonical
        
        # Also map the canonical name to itself
        normalized_canonical = normalize_text(canonical)
        if normalized_canonical:
            normalize[normalized_canonical] = canonical
    
    # Write normalize.json
    normalize_path = os.path.join(core_dir, "normalise.json")
    with open(normalize_path, "w", encoding="utf-8") as f:
        json.dump(normalize, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Generated normalise.json with {len(normalize)} mappings from {len(master)} canonical skills")
    
    # Statistics
    stats = {}
    for canonical, data in master.items():
        cat = data.get("category", "unknown")
        stats[cat] = stats.get(cat, 0) + 1
    
    print("\n📊 Categories:")
    for cat, count in sorted(stats.items()):
        print(f"   {cat}: {count}")
    
    return normalize


if __name__ == "__main__":
    build_normalize_map()
