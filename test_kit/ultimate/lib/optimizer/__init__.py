from .rand import RandOptimizer
from .bo import ConfigedBayesianOptimizer
from .rembo import RemboOptimizer
from .bo_dropout import BOdropoutOptimizer


available_optimizer = [
    'rand',
    'bo',
    'rembo',
    'bodropout'

]


def create_optimizer(name, configs, extra_vars={}):
  assert name in available_optimizer, f'optimizer [{name}] not supported.'
  if name == 'rand':
    return RandOptimizer(configs)
  elif name == 'bo':
    return ConfigedBayesianOptimizer(configs, bo_conf=extra_vars)
  elif name == 'rembo':
    return RemboOptimizer(configs, rembo_conf=extra_vars)
  elif name == 'bodropout':
    return BOdropoutOptimizer(configs, dropout_conf=extra_vars)
