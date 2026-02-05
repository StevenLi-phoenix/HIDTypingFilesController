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
        print("Invalid keys found")
        print(set(content) - set(map.keys()))
        exit(1)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("filename", type=str, nargs="?")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    if args.filename:
        test_keys(args.filename)
    else:
        print("No filename provided")
        exit(1)