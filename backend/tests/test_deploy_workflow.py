"""CI не сообщает об успехе при неверном профиле или недоступном API."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('preflight,health,success', [('1', '0', False), ('0', '1', False), ('0', '0', True)])
def test_deploy_checks_configuration_and_public_api(tmp_path, preflight, health, success):
    workflow = yaml.safe_load((ROOT / '.github/workflows/deploy.yml').read_text())
    script = workflow['jobs']['deploy']['steps'][0]['with']['script']
    script = script.replace('cd /opt/gigaschool', 'cd "$DEPLOY_TEST_DIR"')
    executable = tmp_path / 'bin'
    executable.mkdir()
    docker = executable / 'docker'
    docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$DEPLOY_COMMANDS"
case "$*" in
  "compose run "*) exit "$PREFLIGHT_EXIT" ;;
  "compose exec "*) exit "$HEALTH_EXIT" ;;
esac
exit 0
''')
    docker.chmod(0o755)
    for name in ('git', 'sleep'):
        fake = executable / name
        fake.write_text('#!/bin/sh\nexit 0\n')
        fake.chmod(0o755)
    log = tmp_path / 'commands'
    env = {**os.environ, 'PATH': f'{executable}:{os.environ["PATH"]}', 'DEPLOY_TEST_DIR': str(tmp_path),
           'DEPLOY_COMMANDS': str(log), 'PREFLIGHT_EXIT': preflight, 'HEALTH_EXIT': health}
    result = subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True, timeout=10)
    assert (result.returncode == 0) is success, result.stderr
    commands = log.read_text()
    if preflight != '0':
        assert 'compose up ' not in commands
    elif not success:
        assert 'compose logs --tail=60 backend frontend' in commands
    else:
        assert 'http://127.0.0.1/api/health' in commands
        assert 'compose up -d --force-recreate backend frontend' in commands
    assert ('image prune' in commands) is success
