import re
from pathlib import Path

available_tester = {
    'hibench': re.compile(r'Duration\(s\), (\d+(?:\.\d+)?)')
}

# define a value when configuration's playbooks was failed 
error_value = 600

def parse_result(tester_name, result_dir, task_id, rep, printer):
  assert tester_name in available_tester, f'{tester_name} not available'

  result_file = result_dir / f'{task_id}_run_result_{rep}'
  regexp = available_tester[tester_name]
  if result_file.is_file():
    content = result_file.read_text()
    match = regexp.search(content)
    if match is not None:
      if tester_name == 'hibench':
        return float(match.group(1))
      else:
        printer(f'{task_id} - {rep}: Error. Result Error')
    else:
      printer(f'{task_id} - {rep}: WARNING. Result not match.')
  else:
    printer(f'{task_id} - {rep}: Error. Result file not found. This configurations value is defined by yourself')
    return float(error_value)
    
  return None
