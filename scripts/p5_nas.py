"""P5 dedicated NAS container validation over SSH. Credentials remain local / stdin.

Writes only a fresh .reeldock-p5-<random> child of the configured existing test root.
Optional WebDAV writes reuse P3/P4 random children of P0_TEST_ROOT, synthetic data only.
Does not modify OpenList settings, Docker permissions, networking, or existing containers.
"""

import argparse
import io
import json
import secrets
import shlex
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from dotenv import dotenv_values


class Remote:
    def __init__(self, values):
        self.values = values
        for key in ["P5_SSH_HOST", "P5_SSH_USER", "P5_NAS_TEST_ROOT"]:
            if not values.get(key):
                raise RuntimeError("nas_connection_fields_missing")
        self.parent = PurePosixPath(values["P5_NAS_TEST_ROOT"])
        if (
            not self.parent.is_absolute()
            or self.parent == PurePosixPath("/")
            or ".." in self.parent.parts
        ):
            raise RuntimeError("nas_test_parent_invalid")
        self.folder = str(self.parent / (".reeldock-p5-" + secrets.token_hex(6)))
        self.password = values.get("P5_SUDO_PASSWORD")
        self.sudo = False
        self.sudo_password_required = True
        self.port = str(int(values.get("P5_SSH_PORT") or 22))
        self.known = Path("reports/p5-nas/known_hosts").resolve()
        self.known.parent.mkdir(parents=True, exist_ok=True)
        self.target = values["P5_SSH_USER"] + "@" + values["P5_SSH_HOST"]
        if any(c.isspace() for c in self.target) or self.target.startswith("-"):
            raise RuntimeError("nas_ssh_target_invalid")

    def ssh_args(self, command):
        args = [
            "ssh",
            "-T",
            "-p",
            self.port,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "UserKnownHostsFile=" + str(self.known),
        ]
        if self.values.get("P5_SSH_KEY"):
            args += ["-i", str(Path(self.values["P5_SSH_KEY"]).expanduser())]
        return args + [self.target, command]

    def run(self, command, *, data=b"", timeout=120, sudo=False, check=True):
        if isinstance(command, list):
            command = shlex.join(command)
        if sudo:
            if self.sudo_password_required:
                command = 'sudo -k -S -p "" -- ' + command
                data = (self.password + "\n").encode() + data
            else:
                command = "sudo -n -- " + command
        result = subprocess.run(
            self.ssh_args(command), input=data, capture_output=True, timeout=timeout
        )
        if check and result.returncode:
            raise RuntimeError("nas_sudo_command_failed" if sudo else "nas_ssh_command_failed")
        return result

    def python(self, code, **kwargs):
        return self.run(["python3", "-c", code], **kwargs)

    def preflight(self):
        code = """import json, os, platform, subprocess
from pathlib import Path
r={'ssh':'passed','machine':platform.machine(),'local_test_parent_exists':Path(ROOT).is_dir(),'local_test_parent_writable':os.access(ROOT,os.W_OK)}
fs=subprocess.run(['stat','-f','-c','%T',ROOT],capture_output=True,text=True)
r['test_filesystem']=fs.stdout.strip() if fs.returncode==0 else 'unknown'
p=subprocess.run(['docker','version','--format','{{json .Server}}'],capture_output=True,text=True)
r['docker_access']=p.returncode==0
if p.returncode==0:
 j=json.loads(p.stdout);r['docker_version']=j['Version'];r['docker_arch']=j['Arch']
print(json.dumps(r))
""".replace("ROOT", repr(str(self.parent)))
        info = json.loads(self.python(code).stdout)
        if not info["local_test_parent_exists"] or not info["local_test_parent_writable"]:
            raise RuntimeError("nas_test_parent_unavailable")
        if info.get("test_filesystem") in {"nfs", "nfs4", "cifs", "smb2", "smb3"}:
            raise RuntimeError("nas_database_requires_local_filesystem")
        if not info["docker_access"]:
            result = self.run(
                ["sudo", "-k", "-n", "docker", "version", "--format", "{{json .Server}}"],
                check=False,
            )
            if result.returncode == 0:
                self.sudo_password_required = False
            else:
                if not self.password:
                    raise RuntimeError("nas_docker_permission_requires_sudo_password_or_group")
                result = self.run(["docker", "version", "--format", "{{json .Server}}"], sudo=True)
            daemon = json.loads(result.stdout)
            info.update(
                docker_access=True,
                docker_version=daemon["Version"],
                docker_arch=daemon["Arch"],
                docker_via_sudo=True,
            )
            self.sudo = True
        if info.get("docker_arch") != "amd64":
            raise RuntimeError("nas_architecture_not_validated")
        # Keep only the OpenList image's version tag; no names, labels, paths or environment.
        result = self.run(["docker", "ps", "--format", "{{.Image}}"], sudo=self.sudo)
        for image in result.stdout.decode().splitlines():
            if "openlist" in image.lower():
                info["openlist_image_tag"] = (
                    image.rsplit(":", 1)[-1] if ":" in image else "unspecified"
                )
        return info

    def upload(self, path, source):
        with source.open("rb") as stream:
            result = subprocess.run(
                self.ssh_args("umask 077; cat > " + shlex.quote(path)),
                stdin=stream,
                capture_output=True,
                timeout=900,
            )
        if result.returncode:
            raise RuntimeError("nas_upload_failed")


def local_docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, timeout=900)
    if result.returncode:
        raise RuntimeError("local_docker_command_failed")
    return result


def package_bundle():
    output = io.BytesIO()
    root = Path(__file__).resolve().parents[1]
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name in ["scripts", "backend", "backend/tests"]:
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(info)
        for name in [
            "compose.yaml",
            "scripts/p5_container_smoke.py",
            "scripts/p5_fixture.py",
            "scripts/p3_verify.py",
            "scripts/p4_verify.py",
            "backend/tests/p2_support.py",
            "backend/tests/p3_support.py",
            "backend/tests/p4_support.py",
        ]:
            archive.add(root / name, arcname=name, recursive=False)
    return output.getvalue()


def check_compose(remote, image, port):
    """Run the checked-in Compose file with new local bind directories only."""
    code = """import json, os, subprocess, time
from pathlib import Path
import httpx
root=Path(__ROOT__)
for name in ['compose-data','compose-cache']:
 p=root/name;p.mkdir(mode=0o700,exist_ok=True);os.chown(p,12001,12001);os.chmod(p,0o700)
env=root/'compose.env'
env.write_text('REELDOCK_IMAGE='+__IMAGE__+'\\nREELDOCK_UID=12001\\nREELDOCK_GID=12001\\nREELDOCK_BIND_IP=127.0.0.1\\nREELDOCK_PORT='+str(__PORT__)+'\\nREELDOCK_DATA_PATH='+str(root/'compose-data')+'\\nREELDOCK_CACHE_PATH='+str(root/'compose-cache')+'\\n')
os.chmod(env,0o600)
command=['docker','compose','--project-name',__PROJECT__,'--project-directory',str(root),'--env-file',str(env),'-f',str(root/'compose.yaml')]
def run(*args):
 p=subprocess.run(command+list(args),capture_output=True,text=True,timeout=120)
 if p.returncode:raise RuntimeError('nas_compose_command_failed')
 return p.stdout
try:
 run('config','--quiet');run('up','-d','--no-build')
 deadline=time.monotonic()+90
 with httpx.Client(trust_env=False,timeout=3) as c:
  while True:
   try:
    if c.get('http://127.0.0.1:'+str(__PORT__)+'/api/health').status_code==200:break
   except httpx.HTTPError:pass
   if time.monotonic()>deadline:raise RuntimeError('nas_compose_health_timeout')
   time.sleep(.3)
 identity=run('exec','-T','reeldock','python','-c',
   'import os; print(os.getuid(),os.getgid())').strip()
 assert identity=='12001 12001'
 print(json.dumps({'status':'passed','scope':'checked_in_compose_on_nas','uid':12001,'gid':12001,'health':'passed','bind_storage':'nas_local_dedicated_directory'}))
finally:
 run('down','--timeout','35')
"""
    code = (
        code.replace("__ROOT__", repr(remote.folder))
        .replace("__IMAGE__", repr(image))
        .replace("__PORT__", str(port))
        .replace("__PROJECT__", repr("reeldockp5" + secrets.token_hex(5)))
    )
    try:
        result = remote.python(code, sudo=remote.sudo, timeout=240)
        return json.loads(result.stdout)
    except RuntimeError:
        return {"status": "unverified", "code": "nas_compose_validation_failed"}


def install_bundle(remote):
    remote.run("tar -xpf - -C " + shlex.quote(remote.folder), data=package_bundle())
    code = """from pathlib import Path
root=Path(ROOT)
for name in ['scripts','backend','backend/tests']:
 p=root/name;p.chmod(0o755)
 for source in p.glob('*.py'):source.chmod(0o644)
(root/'compose.yaml').chmod(0o644)
""".replace("ROOT", repr(remote.folder))
    remote.python(code)


def check_openlist(remote, image, env_file):
    values = dotenv_values(env_file, interpolate=False)
    fields = {
        key: values.get(key)
        for key in [
            "P0_WEBDAV_URL",
            "P0_WEBDAV_USERNAME",
            "P0_WEBDAV_PASSWORD",
            "P0_SCAN_PATH",
            "P0_TEST_ROOT",
        ]
    }
    if not fields["P0_WEBDAV_URL"] or not fields["P0_TEST_ROOT"]:
        return {"status": "unverified", "code": "webdav_credentials_missing"}
    code = """import asyncio,json,sys
from reeldock.domain import AppConfig,ProviderError
from reeldock.providers.webdav import WebDAVProvider
v=json.load(sys.stdin)
async def run():
 try:
  c=AppConfig(webdav_url=v['P0_WEBDAV_URL'],
    webdav_username=v['P0_WEBDAV_USERNAME'] or '',webdav_password=v['P0_WEBDAV_PASSWORD'],
    input_path='/test-input',output_path='/test-output',http_timeout_seconds=20)
  async with WebDAVProvider(c) as s:
   counts=[len(await s.list(v[key])) for key in ['P0_SCAN_PATH','P0_TEST_ROOT']]
  print(json.dumps({'status':'passed','scope':'nas_container_real_webdav_read_only','directory_counts':counts,'remote_writes':0,'video_reads':0}))
 except Exception as e:
  code=e.code if isinstance(e,ProviderError) else type(e).__name__
  print(json.dumps({'status':'unverified','code':code}))
asyncio.run(run())
"""
    response = remote.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--platform",
            "linux/amd64",
            "--read-only",
            "--entrypoint",
            "python",
            image,
            "-c",
            code,
        ],
        data=json.dumps(fields).encode(),
        sudo=remote.sudo,
        timeout=90,
    )
    return json.loads(response.stdout)


def check_services(remote, image):
    values = dotenv_values(".env.p2", interpolate=False)
    fields = {
        "token": values.get("P2_TMDB_TOKEN"),
        "tmdb_proxy_url": remote.values.get("P5_TMDB_PROXY_URL"),
    }
    code = (Path(__file__).parent / "p5_network.py").read_text()
    result = remote.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--platform",
            "linux/amd64",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m,mode=1777",
            "--entrypoint",
            "python",
            image,
            "-c",
            code,
        ],
        data=json.dumps(fields).encode(),
        sudo=remote.sudo,
        timeout=180,
    )
    return json.loads(result.stdout)


def check_real_storage(remote, image, env_file):
    """Reuse the P3/P4 dedicated synthetic-package tools, never user media paths."""
    source = dotenv_values(env_file, interpolate=False)
    values = {
        key: source.get(key)
        for key in ["P0_WEBDAV_URL", "P0_WEBDAV_USERNAME", "P0_WEBDAV_PASSWORD", "P0_TEST_ROOT"]
    }
    # P5 validates the product's default automatic 302 handling, without a host list.
    values["P0_REDIRECT_HOSTS"] = ""
    if not values.get("P0_TEST_ROOT") or not values.get("P0_WEBDAV_URL"):
        return {"status": "unverified", "code": "webdav_test_configuration_missing"}
    local = remote.folder + "/real-storage-state-" + secrets.token_hex(4)
    remote.python(
        "import os; from pathlib import Path; p=Path("
        + repr(local)
        + "); p.mkdir(mode=0o700); os.chown(p,12001,12001); os.chmod(p,0o700)",
        sudo=remote.sudo,
    )
    code = """import asyncio,json,sys
from argparse import Namespace
from pathlib import Path
sys.path.insert(0,'/validation/scripts')
import p3_verify,p4_verify
from reeldock.domain import ProviderError
values=json.load(sys.stdin)
p3_verify.dotenv_values=lambda *a,**k:values
p4_verify.dotenv_values=lambda *a,**k:values
original_dav=p3_verify.WebDAVProvider
move_calls={'movie':0,'series':0}
inject_loss=False
injected_timeouts=0
class LostMoveResponseDAV(original_dav):
 async def move(self,source,target):
  global injected_timeouts
  move_calls['series' if inject_loss else 'movie']+=1
  await super().move(source,target)
  # Real server completion, then injected client timeout; never a real network outage claim.
  if inject_loss:
   injected_timeouts+=1
   raise ProviderError('storage_timeout',retryable=True)
p3_verify.WebDAVProvider=LostMoveResponseDAV
async def run():
 global inject_loss
 results={}
 for name,tv in [('movie',False),('series',True)]:
  inject_loss=tv
  try:result=await p3_verify.webdav(Namespace(webdav_env='stdin'),tv=tv)
  except Exception as e:
   results[name]={'status':'failed','code':getattr(e,'code',type(e).__name__)}
   continue
  journal=result.pop('local_journal',None)
  results[name]=result
  if result.get('intent_status') not in {None,'archived'} and journal:
   # Read-only reconciliation forbids PUT/MKCOL/MOVE/Range; original report retained.
   try:
    results[name+'_reconcile']=await p4_verify.reconcile(
      Namespace(journal=Path(journal),webdav_env='stdin'))
   except Exception as e:
    results[name+'_reconcile']={'status':'unverified','code':getattr(e,'code',type(e).__name__)}
 results['fault_injection']={'kind':'timeout_after_real_series_move_response',
   'move_calls':move_calls,'injected_timeouts':injected_timeouts}
 print(json.dumps(results))
asyncio.run(run())
"""
    result = remote.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--platform",
            "linux/amd64",
            "--user",
            "12001:12001",
            "-e",
            "REELDOCK_CACHE_DIR=/work/cache",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m,mode=1777",
            "-v",
            remote.folder + "/scripts:/validation/scripts:ro",
            "-v",
            remote.folder + "/backend/tests:/validation/backend/tests:ro",
            "-v",
            local + ":/work",
            "-w",
            "/work",
            "--entrypoint",
            "python",
            image,
            "-c",
            code,
        ],
        data=json.dumps(values).encode(),
        sudo=remote.sudo,
        timeout=1800,
    )
    return json.loads(result.stdout)


def run(args):
    remote = Remote(dotenv_values(args.env_file, interpolate=False))
    report = {"inventory": remote.preflight(), "release_published": False}
    if args.mode == "preflight":
        return report
    print(json.dumps({"event": "nas_preflight_passed"}), flush=True)
    remote.python(
        "from pathlib import Path; p=Path(" + repr(remote.folder) + "); p.mkdir(mode=0o700)"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    journal = args.output.parent / "private-journal.json"
    journal.write_text(json.dumps({"test_directory": remote.folder}))
    journal.chmod(0o600)
    install_bundle(remote)
    unique_image = "reeldock:p5-" + secrets.token_hex(6)
    journal.write_text(
        json.dumps(
            {
                "test_directory": remote.folder,
                "test_image": unique_image,
                "report": remote.folder + "/container-report.json",
            }
        )
    )
    local_docker("tag", args.image, unique_image)
    try:
        with tempfile.TemporaryDirectory(prefix="reeldock-p5-transfer-") as work:
            archive = Path(work) / "image.tar"
            local_docker("save", "--output", str(archive), unique_image)
            archive.chmod(0o600)
            remote.upload(remote.folder + "/image.tar", archive)
        print(json.dumps({"event": "nas_image_uploaded"}), flush=True)
        remote.run(
            ["docker", "load", "--input", remote.folder + "/image.tar"],
            sudo=remote.sudo,
            timeout=600,
        )
        report["compose"] = check_compose(remote, unique_image, args.port + 1)
        print(
            json.dumps({"event": "nas_compose_checked", "status": report["compose"]["status"]}),
            flush=True,
        )
        output = remote.folder + "/container-report.json"
        result = remote.run(
            [
                "python3",
                remote.folder + "/scripts/p5_container_smoke.py",
                "--image",
                unique_image,
                "--port",
                str(args.port),
                "--fixture-root",
                remote.folder,
                "--output",
                output,
            ],
            sudo=remote.sudo,
            timeout=900,
            check=False,
        )
        try:
            report["container"] = json.loads(result.stdout.decode().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError("nas_container_validation_failed") from None
        report["container"].pop("kodi_fixture_directory", None)
        report["container"]["deployment_environment"] = "user_designated_nas_amd64"
        report["container"]["real_nas"] = (
            "container_runtime_" + report["container"]["status"] + "_mock_services_only"
        )
        report["container"]["scope"] = "nas_container_mock_remote_services_real_ffprobe"
        print(
            json.dumps({"event": "nas_container_checked", "status": report["container"]["status"]}),
            flush=True,
        )
        report["openlist"] = check_openlist(remote, unique_image, args.webdav_env)
        report["external_services"] = check_services(remote, unique_image)
        report["real_storage_packages"] = (
            check_real_storage(remote, unique_image, args.webdav_env)
            if args.webdav_write
            else {"status": "unverified", "code": "dedicated_webdav_writes_not_selected"}
        )
        report["real_media_e2e"] = "unverified"
        report["p5_full_acceptance"] = "unverified"
        report["scope"] = "nas_container_deployment_mock_recovery_and_real_webdav_connection"
        passed = all(
            report[key]["status"] == "passed" for key in ["container", "compose", "openlist"]
        ) and all(
            report["external_services"][key]["status"] == "passed" for key in ["tmdb", "shooter"]
        )
        if args.webdav_write:
            storage = report["real_storage_packages"]
            passed = passed and all(
                storage.get(name, {}).get("status") == "passed"
                or storage.get(name + "_reconcile", {}).get("status") == "passed"
                for name in ["movie", "series"]
            )
        report["status"] = "passed" if passed else "partial"
        # Private journal stays ignored; needed to inspect only this dedicated test directory.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        (args.output.parent / "private-journal.json").write_text(
            json.dumps(
                {"test_directory": remote.folder, "test_image": unique_image, "report": output}
            )
        )
        (args.output.parent / "private-journal.json").chmod(0o600)
        return report
    except Exception as error:
        report.update(
            status="unverified",
            code=str(error) if isinstance(error, RuntimeError) else type(error).__name__,
        )
        return report
    finally:
        local_docker("image", "rm", unique_image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preflight", "validate"])
    parser.add_argument("--env-file", type=Path, default=Path(".env.p5"))
    parser.add_argument("--webdav-env", type=Path, default=Path(".env.p0"))
    parser.add_argument("--image", default="reeldock:0.1.0-local")
    parser.add_argument("--port", type=int, default=18005)
    parser.add_argument("--webdav-write", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("reports/p5-nas/report.json"))
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as error:
        result = {
            "status": "unverified",
            "code": str(error) if isinstance(error, RuntimeError) else type(error).__name__,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    summary = {key: value for key, value in result.items() if key not in {"container"}}
    if "container" in result:
        summary["container_checks"] = len(result["container"].get("checks", []))
    print(json.dumps(summary))
    raise SystemExit(
        0
        if result.get("status") == "passed"
        or (args.mode == "preflight" and result.get("inventory"))
        else 2
    )


if __name__ == "__main__":
    main()
