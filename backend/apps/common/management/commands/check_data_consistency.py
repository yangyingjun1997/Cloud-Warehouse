"""执行只读数据一致性检查，并按问题变化发送钉钉告警。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.common.consistency import build_consistency_issues
from apps.notifications.dingtalk import send_group_message


class Command(BaseCommand):
    help = '检查库存数据一致性；使用 --notify 时按问题变化发送钉钉通知。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--notify', action='store_true',
            help='按首次发现、问题变化和恢复状态发送钉钉群机器人通知。',
        )
        parser.add_argument(
            '--state-file',
            default=getattr(settings, 'CONSISTENCY_STATE_FILE', ''),
            help='通知状态文件路径，默认读取 CONSISTENCY_STATE_FILE。',
        )

    @staticmethod
    def _fingerprint(issues):
        payload = json.dumps(issues, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    @staticmethod
    def _load_state(path):
        if not path.exists():
            return None
        try:
            with path.open('r', encoding='utf-8') as stream:
                state = json.load(stream)
            return state if isinstance(state, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _save_state(path, state):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.name}.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)

    @staticmethod
    def _message(issues):
        lines = [
            f'错误 {sum(item["severity"] == "error" for item in issues)} 项，'
            f'警告 {sum(item["severity"] == "warning" for item in issues)} 项。',
        ]
        for item in issues[:20]:
            lines.append(f'[{item["severity"]}] {item["code"]}：{item["target"]}，{item["message"]}')
        if len(issues) > 20:
            lines.append(f'其余 {len(issues) - 20} 项请登录系统的数据一致性页面查看。')
        return '\n'.join(lines)

    def handle(self, *args, **options):
        issues = build_consistency_issues()
        errors = sum(item['severity'] == 'error' for item in issues)
        warnings = sum(item['severity'] == 'warning' for item in issues)
        self.stdout.write(f'数据一致性检查完成：错误 {errors} 项，警告 {warnings} 项。')
        for item in issues:
            self.stdout.write(
                f'[{item["severity"]}] {item["code"]} | '
                f'{item["object_type"]} {item["target"]} | {item["message"]}'
            )

        if not options['notify']:
            return

        state_path = Path(options['state_file']).expanduser()
        previous = self._load_state(state_path)
        current_has_issues = bool(issues)
        fingerprint = self._fingerprint(issues) if current_has_issues else ''
        previous_has_issues = bool(previous and previous.get('has_issues'))
        problem_changed = (
            current_has_issues
            and (
                previous is None
                or not previous_has_issues
                or previous.get('fingerprint') != fingerprint
            )
        )
        recovered = not current_has_issues and previous_has_issues

        if problem_changed:
            delivered = send_group_message('数据一致性检查告警', self._message(issues))
            if not delivered:
                self.stderr.write(self.style.WARNING('钉钉通知发送失败，本次不更新通知状态，后续可重试。'))
                return
        elif recovered:
            delivered = send_group_message(
                '数据一致性检查已恢复',
                '上次发现的数据一致性问题已全部恢复，当前错误 0 项，警告 0 项。',
            )
            if not delivered:
                self.stderr.write(self.style.WARNING('恢复通知发送失败，本次不更新通知状态，后续可重试。'))
                return

        self._save_state(state_path, {
            'has_issues': current_has_issues,
            'fingerprint': fingerprint,
            'issue_count': len(issues),
        })
        if problem_changed:
            self.stdout.write(self.style.WARNING('已发送一致性问题通知。'))
        elif recovered:
            self.stdout.write(self.style.SUCCESS('已发送一致性恢复通知。'))
        else:
            self.stdout.write('问题状态未变化，不重复发送通知。')
