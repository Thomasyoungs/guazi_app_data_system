from pathlib import Path
from guazi_app_data_system.page_state_machine import PageStateMachine
from guazi_app_data_system.config_loader import load_config
from guazi_app_data_system.action_executor import ActionExecutor
from guazi_app_data_system.audit import AuditLogger
from guazi_app_data_system.exception_handler import IssueRecorder, GuaziFlowError

import tempfile
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    machine = PageStateMachine(load_config('pages.yaml'))
    issues = IssueRecorder(tmp_path / 'issues.jsonl', load_config('exceptions.yaml'))
    audit = AuditLogger(tmp_path / 'audit.jsonl')
    executor = ActionExecutor(machine, load_config('actions.yaml'), audit, issues, dry_run=True)
    try:
        executor.execute('S04', 'click_series_model_button', {'target_series':'series-A','series_row_found':True,'series_model_button_found':False,'actual_click_target':'series-A','actual_click_target_role':'series_name','actual_click_target_series':'series-A'})
        print('NO ERROR, returned OK')
    except GuaziFlowError as e:
        print('RAISED', e.code)
