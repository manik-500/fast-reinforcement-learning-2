# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/05a_data.ipynb (unless otherwise specified).

__all__ = ['is_single_nested_tuple', 'TfmdSourceDL', 'TfmdSource', 'IterableDataBlock', 'SeedZeroWrapper', 'MakeTfm',
           'env_display', 'TestAgent', 'envlen', 'ResetAndStepTfm', 'ExperienceBlock', 'FirstLastTfm',
           'FirstLastExperienceBlock', 'ExperienceFirstLast']

# Cell
from fastai.torch_basics import *
from fastai.data.all import *
from fastai.basics import *
from dataclasses import field,asdict,fields
from typing import List,Any,Dict,Callable
from collections import deque
import gym
from dataclasses import dataclass

if IN_NOTEBOOK:
    from IPython import display
    import PIL.Image

# Cell
def is_single_nested_tuple(b):return isinstance(b,tuple) and len(b)==1 and isinstance(b[0],tuple)

class TfmdSourceDL(TfmdDL):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fitting=False

    def before_iter(self):
        super().before_iter()
        if not self.fitting: self.dataset.reset_src()

    def create_item(self,b):
        b=super().create_item(b)
        return b[0] if is_single_nested_tuple(b) else b

    def after_iter(self):
        super().after_iter()
        if not self.fitting: self.dataset.close_src()

    def after_cancel_fit(self):
        self.dataset.close_src()

# Cell
@delegates()
class TfmdSource(TfmdLists):
    "A `Pipeline` of `tfms` applied to a collection of sources called `items`. Only swtches between them if they get exhausted."
    def __init__(self,items,tfms,n:int=None,cycle_srcs=True,verbose=False,**kwargs):
        self.n=n;self.cycle_srcs=cycle_srcs;self.source_idx=0;self.verbose=verbose;self.res_buffer=deque([]);self.extra_len=0
        super().__init__(items,tfms,**kwargs)

    def __repr__(self): return f"{self.__class__.__name__}: Cycling sources: {self.cycle_srcs}\n{self.items}\ntfms - {self.tfms.fs}"
    def close_src(self):
        [t.close(self) for t in self.tfms if hasattr(t,'close')]
        self.res_buffer.clear()

    def reset_src(self):
        [t.reset(self) for t in self.tfms if hasattr(t,'reset')]
        pv(f'clearing buffer: {self.res_buffer}',self.verbose)
        self.res_buffer.clear()

    def setup(self,train_setup=True):super().setup(train_setup);self.reset_src()

    def __len__(self):
#         return ifnone(self.n,super().__len__()) TODO (Josiah): self.n is not settable in DataBlock, and since TfmdLists gets reinit, this will not persist
        if len(self.items)!=0 and isinstance(self.items[0],gym.Env) and self.cycle_srcs:
            self.reset_src()
            return self.items[0].spec.max_episode_steps+self.extra_len # TODO(Josiah): This is the only OpenAI dependent code. How do we have htis set in setup?
        if self.n is not None: return self.n
        if len(self.items)!=0 and hasattr(self.items[0],'n'):
            return self.items[0].n # TODO(Josiah): Possible solution to make this more generic?
        return super().__len__()

    def __getitem__(self,idx):
        if len(self.res_buffer)!=0:return self.res_buffer.popleft()
        res=super().__getitem__(self.source_idx)
        self.res_buffer.extend([tuple(o) for o in res])
        return self.res_buffer.popleft()

# Cell
class IterableDataBlock(DataBlock):
    tls_type=TfmdSource
    def datasets(self, source, verbose=False):
        self.source = source                     ; pv(f"Collecting items from {source}", verbose)
        items = (self.get_items or noop)(source) ; pv(f"Found {len(items)} items", verbose)
        splits = (self.splitter or RandomSplitter())(items)
        pv(f"{len(splits)} datasets of sizes {','.join([str(len(s)) for s in splits])}", verbose)
        tls=L([self.tls_type(items, t,verbose=verbose) for t in L(ifnone(self._combine_type_tfms(),[None]))])
        return Datasets(items,tls=tls,splits=splits, dl_type=self.dl_type, n_inp=self.n_inp, verbose=verbose)

# Cell
class SeedZeroWrapper(gym.Wrapper):
    def reset(self,*args,**kwargs):
        self.seed(0)
        return super().reset(*args,**kwargs)

class MakeTfm(Transform):
    def setup(self,items:TfmdSource,train_setup=False):
        for i in range(len(items.items)):items.items[i]=SeedZeroWrapper(gym.make(items.items[i]))
        return super().setup(items,train_setup)

# Cell
def env_display(env:gym.Env):
    img=env.render('rgb_array')
    try:display.clear_output(wait=True)
    except AttributeError:pass
    new_im=PIL.Image.fromarray(img)
    display.display(new_im)

# Cell
import ptan


class TestAgent(ptan.agent.BaseAgent):
    def __call__(self,s,ss):return [0]*len(s),[0]*len(s)


def envlen(o:gym.Env):return o.spec.max_episode_steps

@dataclass
class ResetAndStepTfm(Transform):
    def __init__(self,seed:int=None,agent:object=None,n_steps:int=1,steps_delta:int=1,a:Any=None,histories:Dict[str,deque]=None,
                 s:dict=None,steps:dict=None,maxsteps:int=None,display:bool=False,hist2dict:bool=True):
        self.seed=seed;self.agent=agent;self.n_steps=n_steps;self.steps_delta=steps_delta;self.a=a;self.histories=histories;self.hist2dict=hist2dict
        self.maxsteps=maxsteps;self.display=display
        self.s=ifnone(s,{})
        self.steps=ifnone(steps,{})
        self._exhausted=False
        self.exp_src=None
        self.exp_src_iter=None
        # store_attr('n,cycle_srcs', self) TODO (Josiah): Does not seem to work?

    def setup(self,items:TfmdSource,train_setup=False):
#         self.reset(items)
        self.exp_src=ptan.experience.ExperienceSource(items.items, ifnone(self.agent,TestAgent()), steps_count=self.n_steps,steps_delta=self.steps_delta)
        self.exp_src_iter=iter(self.exp_src)
        return super().setup(items,train_setup)

    def reset(self,items):
        if len(items.items)==0:return
        if items.extra_len==0:
            items.extra_len=items.items[0].spec.max_episode_steps*(self.n_steps-1) # Extra steps to unwrap done
        self.exp_src=ptan.experience.ExperienceSource(items.items,ifnone(self.agent,TestAgent()), steps_count=self.n_steps,steps_delta=self.steps_delta)
        self.exp_src_iter=iter(self.exp_src)

    def encodes(self,o:gym.Env):
        exps=next(self.exp_src_iter)
        return exps

# Cell
@delegates(ResetAndStepTfm)
def ExperienceBlock(dls_kwargs=None,**kwargs):
    return TransformBlock(type_tfms=[MakeTfm(),ResetAndStepTfm(**kwargs)],dl_type=TfmdSourceDL,dls_kwargs=dls_kwargs)

# Cell
ExperienceFirstLast = collections.namedtuple('ExperienceFirstLast', ('state', 'action', 'reward', 'last_state','done','episode_reward'))

@delegates(ResetAndStepTfm)
class FirstLastTfm(ResetAndStepTfm):
    def __init__(self,discount=0.99,**kwargs):
        super().__init__(**kwargs)
        self.discount=discount
        self.exp_src_iter=None
        self.exp_src=None

    def setup(self,items:TfmdSource,train_setup=False):
        self.exp_src=iter(ptan.experience.ExperienceSourceFirstLast(items.items,ifnone(self.agent,TestAgent()), steps_count=self.n_steps,steps_delta=self.steps_delta,
                                                                    gamma=self.discount))

        self.exp_src_iter=iter(self.exp_src)
        return super().setup(items,train_setup)

    def reset(self,items):
#         print('reset')
        if len(items.items)==0:return
        if items.extra_len==0:
            items.extra_len=items.items[0].spec.max_episode_steps*(self.n_steps-1) # Extra steps to unwrap done
        self.exp_src=ptan.experience.ExperienceSourceFirstLast(items.items,ifnone(self.agent,TestAgent()), steps_count=self.n_steps,steps_delta=self.steps_delta,
                                                                    gamma=self.discount)
        self.exp_src_iter=iter(self.exp_src)

    def encodes(self,o:gym.Env):
        exps=next(self.exp_src_iter)
        if exps.last_state is None:
            r=self.exp_src.pop_total_rewards()
            if len(r)==0: r=0
            else:         r=r[0]
#             print(r)
            exps=ExperienceFirstLast(state=exps.state,reward=exps.reward,action=exps.action,last_state=exps.state,done=True,episode_reward=r)
        else:exps=ExperienceFirstLast(state=exps.state,reward=exps.reward,action=exps.action,last_state=exps.last_state,done=False,episode_reward=0)
        return [exps]

@delegates(FirstLastTfm)
def FirstLastExperienceBlock(dls_kwargs=None,**kwargs):
    return TransformBlock(type_tfms=[MakeTfm(),FirstLastTfm(**kwargs)],dl_type=TfmdSourceDL,dls_kwargs=dls_kwargs)