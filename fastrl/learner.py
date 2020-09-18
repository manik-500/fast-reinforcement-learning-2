# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/04_learner.ipynb (unless otherwise specified).

__all__ = ['AgentLearner']

# Cell
from fastai.torch_basics import *
from fastai.data.all import *
from fastai.basics import *
from fastai.learner import *
from .basic_agents import *
from dataclasses import field,asdict
from typing import List,Any,Dict,Callable
from collections import deque
import gym

if IN_NOTEBOOK:
    from IPython import display
    import PIL.Image

# Cell
@delegates(Learner)
class AgentLearner(Learner):
    def __init__(self,dls,agent:BaseAgent=BaseAgent(),model=None,use_train_mets=True,**kwargs):
        self.agent=agent
        super().__init__(dls,model=ifnone(model,self.agent.model),**kwargs)
        if use_train_mets:
            for cb in self.cbs:
                if issubclass(cb.__class__,Recorder):cb.train_metrics=True

    def predict(self,s):
        return self.agent(s,None)