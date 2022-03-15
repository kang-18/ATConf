import asyncio
import yaml
import re
import sys
import numpy as np
from datetime import datetime
from pathlib import Path
from statistics import mean

from lib import parse_cmd, run_playbook, get_default, choose_metric_results
from lib.optimizer import create_optimizer
from lib.result_parser import parse_result


def find_exist_task_result():
  task_id = -1
  regexp = re.compile(r'(\d+)_.+')
  if result_dir.exists():
    for p in result_dir.iterdir():
      if p.is_file():
        res = regexp.match(p.name)
        if res:
          task_id = max(task_id, int(res.group(1)))
  return None if task_id == -1 else task_id


def divide_config(sampled_config, os_setting, app_setting):
  for k in sampled_config.keys():
    if type(sampled_config[k]) is bool:
      # make sure no uppercase 'True/False' literal in result
      sampled_config[k] = str(sampled_config[k]).lower()
    elif type(sampled_config[k]) is np.float64:
      sampled_config[k] = float(sampled_config[k])

  sampled_os_config = dict(
      ((k, v) for k, v in sampled_config.items() if k in os_setting)
  )
  sampled_app_config = dict(
      ((k, v) for k, v in sampled_config.items() if k in app_setting)
  )
  return sampled_os_config, sampled_app_config


def _print(msg):
  print(f'[{datetime.now()}] {test_config.task_name} - {msg}')
  # print('[' + datetime.now() + ']')


async def main(test_config, init_id, os_setting, app_setting):
  assert test_config.tune_os or test_config.tune_app, 'at least one of tune_app and tune_os should be True'

  # create optimizer
  optimizer = create_optimizer(
      test_config.optimizer.name,
      {
          **(os_setting if test_config.tune_os else {}),
          **(app_setting if test_config.tune_app else {}),
      },
      extra_vars=test_config.optimizer.extra_vars
  )
  if hasattr(optimizer, 'set_status_file'):
    optimizer.set_status_file(result_dir / 'optimizer_status')

  task_id = init_id
  tester, testee, slave1, slave2, slave3 = test_config.hosts.master, test_config.hosts.master, test_config.hosts.slave1, test_config.hosts.slave2, test_config.hosts.slave3
  while task_id < test_config.optimizer.iter_limit:
    task_id += 1

    # reboot
    # if task_id != 0 and task_id % test_config.optimizer.reboot_interval == 0:
    #   _print('rebooting...')
    #   await run_playbook(
    #       reboot_playbook_path,
    #       host=[tester, testee]
    #   )
    #   _print('reboot finished.')

    # - sample config
    if task_id == 0:  # use default config
      sampled_config_numeric, sampled_config = None, get_default(app_setting)
    else:
      try:
        sampled_config_numeric, sampled_config = optimizer.get_conf()
      except StopIteration:
        # all configuration emitted
        return

    # - divide sampled config app & os
    sampled_os_config, sampled_app_config = divide_config(
        sampled_config,
        os_setting=os_setting,
        app_setting=app_setting
    )
    # if tune_app is off, just give sample_app_config a default value
    if test_config.tune_app is False:
      sampled_app_config = get_default(app_setting)

    # - dump configs
    os_config_path = result_dir / f'{task_id}_os_config.yml'
    os_config_path.write_text(
        yaml.dump(sampled_os_config, default_flow_style=False)
    )
    app_config_path = result_dir / f'{task_id}_app_config.yml'
    app_config_path.write_text(
        yaml.dump(sampled_app_config, default_flow_style=False)
    )
    _print(f'{task_id}: os_config & app_config generated.')

    metric_results_list = []
    skip = False
    for rep in range(test_config.optimizer.repitition):
      await single_test(
          task_name=test_config.task_name,
          task_id=task_id,
          rep=rep,
          tester=tester,
          testee=testee,
          slave1=slave1,
          slave2=slave2,
          slave3=slave3,
          tune_os=(task_id != 0 and test_config.tune_os),
          clients=test_config.clients,
          _skip=skip
      )

      # after test, collect metrics for evaluation
      _print(f'{task_id} - {rep}: parsing result...')
      result = parse_result(
          tester_name=test_config.tester,
          result_dir=result_dir,
          task_id=task_id,
          rep=rep,
          printer=_print
      )
      
      #  The first numerical error may be too large to be added
      if result is not None and rep != 0 and result != 0.:
        metric_results_list.append(- result)
      _print(f'{task_id} - {rep}: done.')
      
      
    # choose the right metric_results
    metric_results = choose_metric_results(metric_results_list)
    print(metric_results)

    # after
    if task_id != 0:  # not adding default info, 'cause default cannot convert to numeric form
      metric_result = mean(metric_results) if len(metric_results) > 0 else .0
      optimizer.add_observation(
          (sampled_config_numeric, metric_result)
      )
      if hasattr(optimizer, 'dump_state'):
        optimizer.dump_state(result_dir / f'{task_id}_optimizer_state')

  # after reaching iter limit
  global proj_root

  # cleanup things
  # _print('experiment finished, cleaning up...')
  # await run_playbook(
  #     playbook_path=proj_root / 'playbooks/cleanup.yml',
  #     task_name=test_config.task_name,
  #     db_name=test_config.target,
  #     host=[test_config.hosts.tester, test_config.hosts.testee],
  # )

  # reboot...
  # _print('rebooting...')
  # await run_playbook(
  #    playbook_path=proj_root / 'playbooks/reboot.yml',
  #    host=[test_config.hosts.tester, test_config.hosts.testee],
  # )
  # _print('done.')


async def single_test(task_name, task_id, rep, tester, testee, slave1, slave2, slave3, tune_os, clients, _skip=False):
  global deploy_spark_playbook_path
  global deploy_hadoop_playbook_path
  global tester_playbook_path
  global osconfig_playbook_path

  # for debugging...
  if _skip:
    return

  _print(f'{task_id}: carrying out #{rep} repetition test...')
  try:

    if task_id == 0 and rep == 0:
      # - deploy db
      _print(f'{task_id} - {rep}: spark_master first deploying...')
      stdout, stderr = await run_playbook(
        deploy_spark_playbook_path,
        host=tester,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
      )
      _print(f'{task_id} - {rep}: spark_master first done.')
      
      _print(f'{task_id} - {rep}: spark_slave1 first deploying...')
      stdout, stderr = await run_playbook(
        deploy_spark_playbook_path,
        host=slave1,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
      )
      _print(f'{task_id} - {rep}: spark_slave1 first done.')

      _print(f'{task_id} - {rep}: spark_slave2 first deploying...')
      stdout, stderr = await run_playbook(
        deploy_spark_playbook_path,
        host=slave2,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
      )
      _print(f'{task_id} - {rep}: spark_slave2 first done.')
      
      _print(f'{task_id} - {rep}: spark_slave3 first deploying...')
      stdout, stderr = await run_playbook(
        deploy_spark_playbook_path,
        host=slave3,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
      )
      _print(f'{task_id} - {rep}: spark_slave3 first done.')

      _print(f'{task_id} - {rep}: hadoop_slave1 first deploying...')
      stdout_hadoop, stderr_hadoop = await run_playbook(
        deploy_hadoop_playbook_path,
        host=slave1,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
      )
      _print(f'{task_id} - {rep}: hadoop_slave1 first done.')


      _print(f'{task_id} - {rep}: hadoop_slave2 first deploying...')
      stdout_hadoop, stderr_hadoop = await run_playbook(
        deploy_hadoop_playbook_path,
        host=slave2,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
      )
      _print(f'{task_id} - {rep}: hadoop_slave2 first done.')

      _print(f'{task_id} - {rep}: hadoop_slave3 first deploying...')
      stdout_hadoop, stderr_hadoop = await run_playbook(
        deploy_hadoop_playbook_path,
        host=slave3,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
      )
      _print(f'{task_id} - {rep}: hadoop_slave3 first done.')
   
      _print(f'{task_id} - {rep}: hadoop_master first deploying...')
      stdout_hadoop, stderr_hadoop = await run_playbook(
        deploy_hadoop_playbook_path,
        host=tester,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
      )
      _print(f'{task_id} - {rep}: hadoop_master first done.')


    if tune_os:
      # os parameters need to be changed
      _print(f'{task_id} - {rep}: setting os parameters...')
      await run_playbook(
          osconfig_playbook_path,
          host=tester,
          task_name=task_name,
          task_id=task_id,
      )
    else:
      # - no need to change, for default testing or os test is configured to be OFF
      _print(
          f'{task_id} - {rep}: resetting os  parameters...')
      await run_playbook(
          osconfig_playbook_path,
          host=tester,
          task_name=task_name,
          task_id=task_id,
          tags='cleanup'
      )
    _print(f'{task_id} - {rep}: done.')

    # - launch test and fetch result
    _print(f'{task_id} - {rep}: hibench testing...')
    await run_playbook(
        tester_playbook_path,
        host=testee,
        target=tester,
        task_name=task_name,
        task_id=task_id,
        task_rep=rep,
        workload_path=str(workload_path),
        n_client=clients
    )
    _print(f'{task_id} - {rep}: hibench done.')

    # - cleanup os config
    _print(f'{task_id} - {rep}: cleaning up os config...')
    await run_playbook(
        osconfig_playbook_path,
        host=tester,
        tags='cleanup'
    )
    _print(f'{task_id} - {rep}: done.')
  except RuntimeError as e:
    errlog_path = result_dir / f'{task_id}_error_{rep}.log'
    errlog_path.write_text(str(e))
    print(e)

# -------------------------------------------------------------------------------------------------------
#
test_config = parse_cmd()

assert test_config is not None

# calculate paths
proj_root = Path(__file__, '../../..').resolve()

db_dir = proj_root / f'target/{test_config.target}'
result_dir = db_dir / f'results/{test_config.task_name}'
setting_path = proj_root / \
    f'target/{test_config.target}/os_configs_info.yml'
deploy_spark_playbook_path = db_dir / 'playbook/deploy_spark.yml'
deploy_hadoop_playbook_path = db_dir / 'playbook/deploy_hadoop.yml'
tester_playbook_path = db_dir / 'playbook/tester.yml'
osconfig_playbook_path = db_dir / 'playbook/set_os.yml'
reboot_playbook_path = db_dir / 'playbook/reboot.yml'
workload_path = db_dir / f'workload/{test_config.workload}'
os_setting_path = proj_root / \
    f'target/{test_config.target}/os_configs_info.yml'
app_setting_path = proj_root / \
    f'target/{test_config.target}/app_configs_info.yml'


init_id = -1

# check existing results, find minimum available task_id
exist_task_id = find_exist_task_result()
if exist_task_id is not None:
  _print(f'previous results found, with max task_id={exist_task_id}')
  policy = test_config.exist
  if policy == 'delete':
    for file in sorted(result_dir.glob('*')):
      file.unlink()
    _print('all deleted')
  elif policy == 'continue':
    _print(f'continue with task_id={exist_task_id + 1}')
    init_id = exist_task_id
  else:
    _print('set \'exist\' to \'delete\' or \'continue\' to specify what to do, exiting...')
    sys.exit(0)

# create dirs
result_dir.mkdir(parents=True, exist_ok=True)

# dump test configs
(result_dir / 'test_config.yml').write_text(
    yaml.dump(test_config, default_flow_style=False)
)
_print('test_config.yml dumped')

# read parameters for tuning
os_setting = yaml.load(os_setting_path.read_text())  # pylint: disable=E1101
app_setting = yaml.load(app_setting_path.read_text())  # pylint: disable=E1101

#event loop, main() is async
loop = asyncio.get_event_loop()
loop.run_until_complete(
    main(
        test_config=test_config,
        init_id=init_id,
        os_setting=os_setting,
        app_setting=app_setting
    )
)
loop.close()
