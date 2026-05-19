"""test_db.py — Diagnostic connexion TimescaleDB via subprocess"""
import subprocess

CONTAINER = "raffinerie-iot-timescaledb-1"
DB_USER   = "admin"
DB_NAME   = "iotdb"

print("=== Test 1 : shell=False, flag -c ===")
cmd = ["docker", "exec", "-i", CONTAINER,
       "psql", "-U", DB_USER, "-d", DB_NAME,
       "-c", "SELECT COUNT(*) FROM capteurs;"]
r = subprocess.run(cmd, capture_output=True, shell=False)
print("returncode:", r.returncode)
print("stdout:", r.stdout.decode("utf-8", errors="replace"))
print("stderr:", r.stderr.decode("utf-8", errors="replace"))

print("\n=== Test 2 : shell=False, stdin ===")
cmd2 = ["docker", "exec", "-i", CONTAINER,
        "psql", "-U", DB_USER, "-d", DB_NAME,
        "-t", "-A", "-F", "|"]
r2 = subprocess.run(cmd2, input=b"SELECT COUNT(*) FROM capteurs;",
                    capture_output=True, shell=False)
print("returncode:", r2.returncode)
print("stdout:", r2.stdout.decode("utf-8", errors="replace"))
print("stderr:", r2.stderr.decode("utf-8", errors="replace"))

print("\n=== Test 3 : shell=True, string ===")
cmd3 = f'docker exec -i {CONTAINER} psql -U {DB_USER} -d {DB_NAME} -c "SELECT COUNT(*) FROM capteurs;"'
r3 = subprocess.run(cmd3, capture_output=True, shell=True)
print("returncode:", r3.returncode)
print("stdout:", r3.stdout.decode("utf-8", errors="replace"))
print("stderr:", r3.stderr.decode("utf-8", errors="replace"))
