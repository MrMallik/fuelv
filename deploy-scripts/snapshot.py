import json
import sys

with open(sys.argv[1]) as stream:
    config = json.load(stream)
with open(sys.argv[2]) as stream:
    containers = json.load(stream)
expected = {"fuelv"}
if set(config["services"]) != expected or {
    item["Config"]["Labels"].get("com.docker.compose.service") for item in containers
} != expected:
    raise SystemExit("Expected exactly the fuelv service")
for container in containers:
    labels = container["Config"]["Labels"]
    name = labels.get("com.docker.compose.service")
    if name not in expected or labels.get("com.docker.compose.project") != "fuelv-production":
        raise SystemExit("Unexpected Compose project or service; refusing replacement")
    if container["State"].get("Health", {}).get("Status") != "healthy":
        raise SystemExit("Existing fuelv container must be healthy before deployment")
    service = config["services"][name]
    service["image"] = container["Image"]
    service.pop("build", None)
with open(sys.argv[3], "w") as stream:
    json.dump(config, stream)
