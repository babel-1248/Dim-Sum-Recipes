import sys, json

data = json.loads(sys.argv[1])
with open('feed_state.json', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
