from pathlib import Path
import unittest
from unittest.mock import patch

from lightacademia.agents import AgentContext, ClaudeCliAgent, CodexSdkAgent


class DailyRobotAccessTest(unittest.TestCase):
    def context(self, daily):
        root = Path('/notebook')
        return AgentContext(
            project_dir=root / ('.daily-notes' if daily else 'SemanticFusion'),
            project_name='.daily-notes' if daily else 'SemanticFusion',
            tools_dir=Path('/missing-tools'),
            current_note='2026-11-24.md' if daily else 'DiTTS.md',
            notebook_dir=root if daily else None,
        )

    def test_prompt_scope_is_expanded_only_for_daily_robot(self):
        agent = CodexSdkAgent()
        daily = agent._build_prompt('Read SemanticFusion/DiTTS.md', self.context(True))
        self.assertIn('Notebook root: /notebook', daily)
        self.assertIn('may read and update any project', daily)
        self.assertIn('do not need the user to switch', daily)
        self.assertNotIn('Do not read or update sibling projects', daily)
        self.assertNotIn('Do not access folders outside the selected project root', daily)
        regular = agent._build_prompt('Read this note', self.context(False))
        self.assertIn('Do not read or update sibling projects', regular)
        self.assertNotIn('may read and update any project', regular)
        self.assertNotIn('{{workspace_access_rules}}', daily + regular)

    def test_claude_adds_notebook_directory_only_for_daily_robot(self):
        for daily in (True, False):
            with self.subTest(daily=daily):
                command = ClaudeCliAgent()._build_command('claude', self.context(daily), Path('/tools-copy'))
                self.assertEqual('/notebook' in command, daily)
                self.assertIn('/tools-copy', command)

    @patch('lightacademia.agents.Codex')
    def test_codex_action_and_new_and_resumed_chat_get_matching_permissions(self, codex_class):
        codex = codex_class.return_value.__enter__.return_value
        codex.thread_start.return_value.turn.return_value.stream.return_value = []
        codex.thread_resume.return_value.turn.return_value.stream.return_value = []
        for daily in (True, False):
            for mode in ('action', 'new_chat', 'resumed_chat'):
                with self.subTest(daily=daily, mode=mode):
                    context = self.context(daily)
                    agent = CodexSdkAgent()
                    if mode == 'action':
                        agent.run('Read the note', context)
                    else:
                        agent.chat('Read the note', context,
                                   details={'thread_id': 'existing'} if mode == 'resumed_chat' else {})
                    call = codex.thread_resume.call_args if mode == 'resumed_chat' else codex.thread_start.call_args
                    roots = call.kwargs['config']['sandbox_workspace_write']['writable_roots']
                    self.assertEqual('/notebook' in roots, daily)
                    self.assertEqual(call.kwargs['cwd'], str(context.project_dir))
                    self.assertTrue(any(root.endswith('/tools') for root in roots))

    def test_app_passes_notebook_scope_only_from_daily_notes(self):
        import threading
        from types import SimpleNamespace
        from app import _agent_worker
        from lightacademia.agents import AgentChatResult
        from lightacademia.storage import Project

        class FakeChatAgent:
            supports_chat = True
            name = 'fake'

            def chat(self, prompt, context, **kwargs):
                self.context = context
                return AgentChatResult(response='Done')

        for daily in (True, False):
            with self.subTest(daily=daily):
                expected = self.context(daily)
                state = SimpleNamespace(
                    agent='codex', agent_timeout_seconds=60, codex_model=None,
                    project=Project(expected.project_name, expected.project_dir),
                    tools_dir=expected.tools_dir, note_name=expected.current_note,
                    mode='chat', prompt='Read DiTTS', chat_details={},
                    stop_requested=threading.Event(), run_id='test', error=None,
                )
                agent = FakeChatAgent()
                with patch('app.default_agent', return_value=agent):
                    _agent_worker(state)
                self.assertIsNone(state.error)
                self.assertEqual(agent.context.notebook_dir, expected.notebook_dir)
                self.assertTrue(state.done)
