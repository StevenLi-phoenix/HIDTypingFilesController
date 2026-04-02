import argparse
import main
import os

def test_keys(file: str):
    map = main.build_map()
    assert os.path.exists(file), f"File not found: {file}"
    content = open(file, "r").read()

    try:    
        assert all(key in map.keys() for key in set(content))
        print("All keys are valid")
    except AssertionError:
        print(f"Invalid keys found @ {file}")
        print(set(content) - set(map.keys()))
        # utf-8 encode the keys
        for key in set(content) - set(map.keys()):
            print(f"{key} -> {key.encode("utf-8")}")
        exit(1)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("filenames", type=str, nargs="+")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    for filename in args.filenames:
        test_keys(filename)