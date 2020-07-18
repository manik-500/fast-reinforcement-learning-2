# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/05_data_block.ipynb (unless otherwise specified).

__all__ = ['fix_s', 'Experience', 'ExperienceSourceCallback', 'ExperienceSourceDataset',
           'FirstLastExperienceSourceDataset', 'AsyncExperienceSourceCallback', 'AsyncGradExperienceSourceCallback',
           'AsyncDataExperienceSourceCallback', 'safe_fit', 'grad_fitter', 'data_fitter',
           'AsyncGradExperienceSourceDataset', 'AsyncDataExperienceSourceDataset', 'DatasetDisplayWrapper',
           'ExperienceSourceDataBunch', 'AsyncExperienceSourceDataBunch', 'dqn_fitter', 'dqn_grad_fitter',
           'buggy_dqn_fitter']

# Cell
from fastai.basic_data import *
from fastai.basic_train import *
from fastai.torch_core import *
from fastai.callbacks import *
from .wrappers import *
from .basic_agents import *
from .metrics import *
from dataclasses import asdict
from functools import partial
from fastprogress.fastprogress import IN_NOTEBOOK
from fastcore.utils import *
import torch.multiprocessing as mp
from functools import wraps
from queue import Empty
import textwrap
import logging
import gym

logging.basicConfig(format='[%(asctime)s] p%(process)s line:%(lineno)d %(levelname)s - %(message)s',
                    datefmt='%m-%d %H:%M:%S')
_logger=logging.getLogger(__name__)

# Cell
def fix_s(x):
    "Flatten `x` to `(1,-1)` where `1` is the batch dim (B) unless **seems** to be an image e.g. has 3 dims (W,H,C), then it will attempt (B,W,H,C)."
    return (x if x.shape[0]==1 else
            x.reshape(1,-1) if len(x.shape)==2 else
            np.expand_dims(x,0))

@dataclass
class Experience(object):
    s:np.array
    sp:np.array
    a:np.array
    r:np.array
    d:np.array
    agent_s:np.array

    # TODO possibly have x,y for more generic experience integration with datasets.

#     def __post_init__(self):
#         for k,v in asdict(self).items():
#             setattr(self,k,fix_s(v) if k in ['s','sp'] else np.array(v,dtype=float).reshape(1,-1))

# Cell
class ExperienceSourceCallback(LearnerCallback):
    def on_train_begin(self,*args,**kwargs):
        self.learn.data.train_dl.dataset.learn=self.learn
        if not self.learn.data.empty_val:
            self.learn.data.valid_dl.dataset.learn=self.learn

class ExperienceSourceDataset(Dataset):
    "Similar to fastai's `LabelList`, iters in-order samples from `1->len(envs)` `envs`."
    def __init__(self,env:str,n_envs=1,steps=1,max_episode_step=None,pixels=False):
        def make_env():
            env=gym.make("CartPole-v1")
            if pixels:env.reset()
            return env

        self.envs=[make_env() for _ in range(n_envs)]
        if pixels:self.envs=[PixelObservationWrapper(e) for e in self.envs]
        self.steps=steps
        self.max_episode_step=max_episode_step
        self.d=np.zeros((len(self.envs),))+1
        self.s=np.zeros((len(self.envs),*self.envs[0].observation_space.sample().shape))
        self.iterations=np.zeros((len(self.envs)))
        self.current_r=np.zeros((len(self.envs)))
        self.total_r=[]
        self.total_iterations=[]
        self.learn=None
        self._warned=False
        self.callback_fns=[ExperienceSourceCallback]
        self.inc=0

    def __len__(self): return ifnone(self.max_episode_step,self.envs[0].spec.max_episode_steps)*len(self.envs)

    def get_action(self,idx):
        if self.learn is None:
            if not self._warned:_logger.warning('`self.learn` is None. will use random actions instead.')
            self._warned=True
            return self.envs[0].action_space.sample(),np.zeros((1,1))
        return self.learn.predict(self.s[idx])

    @log_args
    def __getitem__(self,_):
        idx=self.inc
        _logger.debug('Idx:%s,Iter:%s',idx,self.iterations[idx])
        if idx==0 and self.iterations[idx]==0:
            for i,e in enumerate(self.envs): self.s[i]=e.reset() # There is the possiblity this will equal None (and crash)?
            self.iterations=np.zeros((len(self.envs)),dtype=int)
            self.current_r=np.zeros((len(self.envs)))

        exps:List[Experience]=[]
        while True:
            a,agent_s=self.get_action(idx)
            sp,r,self.d[idx],_=self.envs[idx].step(a)
            self.current_r[idx]+=r
            exps.append(Experience(self.s[idx],sp,a,r,self.d[idx],agent_s=ifnone(agent_s,[])))
            self.s[idx]=sp
            self.iterations[idx]+=1
            if self.d[idx] or self.iterations[idx]>=len(self)-1:
                self.total_r.append(self.current_r[idx])
                self.total_iterations.append(self.iterations[idx])
                self.iterations[idx]=0
                if self.inc>=len(self.envs)-1:self.inc=0
                else:                         self.inc+=1
                if idx>=len(self.envs):raise StopIteration()
                break
            if len(exps)%self.steps==0:
                break
        return [e.s for e in exps],[asdict(e) for e in exps]

    def pop_total_r(self):
        r=self.total_r
        if r:self.total_r,self.total_steps=[],[]
        return r

    def pop_r_interations(self):
        res = list(zip(self.total_r, self.total_iterations))
        if res:self.total_r,self.total_iterations=[],[]
        return res

# Cell
class FirstLastExperienceSourceDataset(ExperienceSourceDataset):
    "Similar to `ExperienceSourceDataset` but only keeps the first and last parts of a step. Can be seen as frame skipping."
    def __init__(self,*args,discount=0.99,**kwargs):
        super(FirstLastExperienceSourceDataset,self).__init__(*args,**kwargs)
        self.discount=discount

    @log_args
    def __getitem__(self,idx):
        s,exps=super(FirstLastExperienceSourceDataset,self).__getitem__(idx)
        exp=exps[-1]
        exp['s']=exps[0]['s']

        total_reward=0.0
        for e in reversed(exps):
            total_reward*=self.discount
            total_reward+=e['r']
        exp['r']=total_reward

        return [exps[0]['s']],[exp]

# Cell
class AsyncExperienceSourceCallback(LearnerCallback):
    _order = -11

    def on_epoch_begin(self,**kwargs):
        ds=(self.learn.data.train_ds if self.learn.model.training or self.learn.data.empty_val else
            self.learn.data.valid_ds)
        if not self.learn.data.empty_val:ds.pause_event.clear()

        if len(ds.data_proc_list)==0:
            if not hasattr(self.learn,'fitter'):
                _logger.warning('Using the default fitter function which will likely not work. Make sure your `AgentLearner` has a `fitter` attribute to actually run/train.')

            for proc_idx in range(ds.n_processes):
                _logger.info('Starting Process')
                data_proc=self.load_process()
                data_proc.start()
                ds.data_proc_list.append(data_proc)

    def load_process(self):raise NotImplementedError()
    def empty_queues(self):raise NotImplementedError()

    def on_batch_end(self,**kwargs):
        # If not training, pause train ds, otherwise pause valid ds
        ds=(self.learn.data.train_ds if not self.learn.model.training or self.learn.data.empty_val else
            self.learn.data.valid_ds)
        if not self.learn.data.empty_val:ds.pause_event.set()

    def on_train_end(self,**kwargs):
        for ds in [self.learn.data.train_ds,None if self.learn.data.empty_val else self.learn.data.valid_ds]:
            if ds is None: continue
            ds.cancel_event.set()
            for _ in range(5):
                self.empty_queues()
            for proc in ds.data_proc_list: proc.join(timeout=0)

class AsyncGradExperienceSourceCallback(AsyncExperienceSourceCallback):
    def load_process(self):
        ds=(self.learn.data.train_ds if self.learn.model.training or self.learn.data.empty_val else
            self.learn.data.valid_ds)
        return mp.Process(target=getattr(self.learn,'fitter',grad_fitter),
                          args=(self.learn.model,self.learn.agent,ds.ds_cls),
                          kwargs={'grad_queue':ds.grad_queue,'loss_queue':ds.loss_queue,'pause_event':ds.pause_event,'cancel_event':ds.cancel_event,
                                  'metric_queue':ds.metric_queue})
    def empty_queues(self):
        ds=(self.learn.data.train_ds if self.learn.model.training or self.learn.data.empty_val else
            self.learn.data.valid_ds)
        while not ds.grad_queue.empty(): ds.grad_queue.get()
        while not ds.loss_queue.empty(): ds.loss_queue.get()

class AsyncDataExperienceSourceCallback(AsyncExperienceSourceCallback):
    def load_process(self):
        ds=(self.learn.data.train_ds if self.learn.model.training or self.learn.data.empty_val else
            self.learn.data.valid_ds)
        return mp.Process(target=getattr(self.learn,'fitter',data_fitter),
                          args=(self.learn.model,self.learn.agent,ds.ds_cls),
                          kwargs={'data_queue':ds.data_queue,'pause_event':ds.pause_event,'cancel_event':ds.cancel_event,
                                  'metric_queue':ds.metric_queue})
    def empty_queues(self):
        ds=(self.learn.data.train_ds if self.learn.model.training or self.learn.data.empty_val else
            self.learn.data.valid_ds)
        while not ds.data_queue.empty():ds.data_queue.get()

# Cell
def safe_fit(f):
    @wraps(f)
    def wrap(*args,cancel_event,**kwargs):
        try:
            return f(*args,cancel_event=cancel_event,**kwargs)
        finally:
            cancel_event.set()
            for k,v in kwargs.items():
                if k.__contains__('queue') and v is not None:v.put(None)
            return None
    return wrap

@safe_fit
def grad_fitter(model:nn.Module,agent:BaseAgent,ds:ExperienceSourceDataset,grad_queue:mp.JoinableQueue,
                loss_queue:mp.JoinableQueue,pause_event:mp.Event,cancel_event:mp.Event,metric_queue:mp.JoinableQueue=None):
    "Updates a `train_queue` with `model.parameters()` and `loss_queue` with the loss. Note that this is only an example grad_fitter."
    while not cancel_event.is_set(): # We are expecting the  grad_fitter to loop unless cancel_event is set
        cancel_event.wait(0.1)
        grad_queue.put(None)         # Adding `None` to `train_queue` will trigger an eventual ending of training
        loss_queue.put(None)
        if pause_event.is_set():     # There needs to be the ability for the grad_fitter to pause e.g. if waiting for validation to end.
            cancel_event.wait(0.1)   # Using cancel_event to wait allows the main process to end this Process.
        break

@safe_fit
def data_fitter(model:nn.Module,agent:BaseAgent,ds:ExperienceSourceDataset,data_queue:mp.JoinableQueue,
                pause_event:mp.Event,cancel_event:mp.Event,metric_queue:mp.JoinableQueue=None):
    _logger.warning('Using the `test_fitter` function. Make sure your `AgentLearner` has a `data_fitter` to actually run/train.')
    while not cancel_event.is_set(): # We are expecting the  grad_fitter to loop unless cancel_event is set
        cancel_event.wait(0.1)
        data_queue.put(None)         # Adding `None` to `train_queue` will trigger an eventual ending of training
        if pause_event.is_set():     # There needs to be the ability for the grad_fitter to pause e.g. if waiting for validation to end.
            cancel_event.wait(0.1)   # Using cancel_event to wait allows the main process to end this Process.

def _soft_queue_get(q:mp.Queue,e:mp.Event):
    entry=None
    while not e.is_set():
        try:
            entry=q.get_nowait()
            break
        except Empty:pass
    return entry

class AsyncGradExperienceSourceDataset(ExperienceSourceDataset):
    "Contains dataloaders of multiple sub-datasets and executes them using `n_processes`. `xb` is the gradients from the agents, `yb` is the loss."
    def __init__(self,env_name:str,n_envs=1,ds_cls=ExperienceSourceDataset,max_episode_step=None,n_processes=1,queue_sz=None,*args,**kwargs):
        self.n_processes=n_processes
        self.n_envs=n_envs
        self.env_name=env_name
        self.ds_cls=ds_cls
        self.pause_event=mp.Event()                               # If the event is set, then the Process will freeze.
        self.cancel_event=mp.Event()                              # If the event is set, then the Process will freeze.
        self.max_episode_step=max_episode_step
        self.queue_sz=ifnone(queue_sz,self.n_processes*n_envs)
        self.grad_queue=mp.JoinableQueue(maxsize=self.queue_sz)
        self.loss_queue=mp.JoinableQueue(maxsize=self.queue_sz)
        self.metric_queue:mp.JoinableQueue=None
        self.data_proc_list=[]
        self.callback_fns=[AsyncGradExperienceSourceCallback]
        self._env=gym.make(self.env_name)

    def __len__(self): return ifnone(self.max_episode_step,self._env.spec.max_episode_steps)*self.n_envs


    def __getitem__(self,idx):
        if len(self.data_proc_list)==0: raise StopIteration()
        train_entry=_soft_queue_get(self.grad_queue,self.cancel_event)

        if train_entry is None:
            raise StopIteration()

        train_loss_entry=_soft_queue_get(self.loss_queue,self.cancel_event)
        return train_entry,[train_loss_entry]

class AsyncDataExperienceSourceDataset(ExperienceSourceDataset):
    "Contains dataloaders of multiple sub-datasets and executes them using `n_processes`. `xb` is the gradients from the agents, `yb` is the loss."
    def __init__(self,env_name:str,n_envs=1,ds_cls=ExperienceSourceDataset,max_episode_step=None,n_processes=1,queue_sz=None,**kwargs):
        self.n_processes=n_processes
        self.n_envs=n_envs
        self.ds_cls=ds_cls
        self.env_name=env_name
        self.pause_event=mp.Event()                               # If the event is set, then the Process will freeze.
        self.cancel_event=mp.Event()                              # If the event is set, then the Process will freeze.
        self.max_episode_step=max_episode_step
        self.queue_sz=ifnone(queue_sz,self.n_processes*n_envs)
        self.data_queue=mp.JoinableQueue(maxsize=self.n_processes)
        self.metric_queue:mp.JoinableQueue=None
        self.data_proc_list=[]
        self.callback_fns=[AsyncDataExperienceSourceCallback]
        self._env=gym.make(self.env_name)

    def __len__(self): return ifnone(self.max_episode_step,self._env.spec.max_episode_steps)*self.n_envs

    def __getitem__(self,idx):
        if len(self.data_proc_list)==0: raise StopIteration()
        train_entry=_soft_queue_get(self.data_queue,self.cancel_event)

        if train_entry is None:
            raise StopIteration()

        return [e['s'] for e in train_entry],train_entry

# Cell
if IN_NOTEBOOK:
    from IPython import display
    import PIL.Image

# Cell
class DatasetDisplayWrapper(object):
    def __init__(self,ds,rows=2,cols=2,max_w=800):
        "Wraps a ExperienceSourceDataset instance showing multiple envs in a `rows` by `cols` grid in a Jupyter notebook."
        # Ref: https://stackoverflow.com/questions/1443129/completely-wrap-an-object-in-python
        # We are basically Wrapping any instance of ExperienceSourceDataset (kind of cool right?)
        clss=(ExperienceSourceDataset,FirstLastExperienceSourceDataset,
              AsyncGradExperienceSourceDataset,AsyncDataExperienceSourceDataset)
        assert issubclass(ds.__class__,clss),'Currently this only works with the ExperienceSourceDataset and Async*ExperienceSourceDataset class only.'
        self.__class__ = type(ds.__class__.__name__,(self.__class__, ds.__class__),{})
        self.__dict__=ds.__dict__
        self.rows,self.cols,self.max_w=rows,cols,max_w
        self.current_display=None
        if not IN_NOTEBOOK:
            _logger.warning('It seems you are not running in a notebook. Nothing is going to be displayed.')
            return

        if self.envs[0].render('rgb_array') is None: self.envs[0].reset()
        rdr=self.envs[0].render('rgb_array')
        if rdr.shape[1]*self.cols>max_w:
            _logger.warning('Max Width is %s but %s*%s is greater than. Decreasing the number of cols to %s, rows increase by %s',
                            max_w,rdr.shape[1],self.cols,max_w%rdr.shape[1],max_w%rdr.shape[1])
            self.cols=max_w%rdr.shape[1]
            self.rows+=max_w%rdr.shape[1]
        self.max_displays=self.cols*self.rows
        self.current_display=np.zeros(shape=(self.rows*rdr.shape[0],self.cols*rdr.shape[1],rdr.shape[2])).astype('uint8')
        _logger.info('%s, %s, %s, %s, %s',0,0//self.cols,0%self.cols,rdr.shape,self.current_display.shape)

    def __getitem__(self,idx):
        o=super(DatasetDisplayWrapper,self).__getitem__(idx)
        idx=idx%self.max_displays
        if self.current_display is not None and idx<self.rows*self.cols:
            display.clear_output(wait=True)
            im=self.envs[idx].render(mode='rgb_array')
            self.current_display[(idx//self.cols)*im.shape[0]:(idx//self.cols)*im.shape[0]+im.shape[0],
                                 (idx%self.cols)*im.shape[1]:(idx%self.cols)*im.shape[1]+im.shape[1],:]=im
            new_im=PIL.Image.fromarray(self.current_display)
            display.display(new_im)
        else:
            display.display(PIL.Image.fromarray(self.current_display))
        return o

# Cell
class ExperienceSourceDataBunch(DataBunch):
    @classmethod
    def from_env(cls,env:str,n_envs=1,firstlast=False,display=False,max_steps=None,skip_step=1,path:PathOrStr='.',add_valid=True,
                 cols=1,rows=1,max_w=800):
        def create_ds(make_empty=False):
            _ds_cls=FirstLastExperienceSourceDataset if firstlast else ExperienceSourceDataset
            make_env = lambda: gym.make(env)
            envs=[make_env() for _ in range(n_envs)]
            _ds=_ds_cls(envs,max_episode_step=0 if make_empty else max_steps,steps=skip_step)
            if display:_ds=DatasetDisplayWrapper(_ds,cols=cols,rows=rows,max_w=max_w)
            return _ds

        dss=(create_ds(),create_ds(not add_valid))
        return cls.create(*dss,bs=n_envs,num_workers=0)

    @classmethod
    def create(cls, train_ds:Dataset, valid_ds:Dataset, test_ds:Optional[Dataset]=None, path:PathOrStr='.', bs:int=64,
               val_bs:int=None, num_workers:int=defaults.cpus, dl_tfms:Optional[Collection[Callable]]=None,
               device:torch.device=None, collate_fn:Callable=data_collate, no_check:bool=False, **dl_kwargs)->'DataBunch':
        "Create a `DataBunch` from `train_ds`, `valid_ds` and maybe `test_ds` with a batch size of `bs`. Passes `**dl_kwargs` to `DataLoader()`"
        datasets = cls._init_ds(train_ds, valid_ds, test_ds)
        val_bs = ifnone(val_bs, bs)
        dls = [DataLoader(d, b, shuffle=s, drop_last=s, num_workers=num_workers, **dl_kwargs) for d,b,s in
               zip(datasets, (bs,val_bs,val_bs,val_bs), (False,False,False,False)) if d is not None]
        return cls(*dls, path=path, device=device, dl_tfms=dl_tfms, collate_fn=collate_fn, no_check=no_check)

# Cell
class AsyncExperienceSourceDataBunch(ExperienceSourceDataBunch):
    @classmethod
    def from_env(cls,env:str,n_envs=1,data_exp=True,firstlast=False,display=False,max_steps=None,skip_step=1,path:PathOrStr='.',add_valid=True,
                 cols=1,rows=1,max_w=800,n_processes=1,queue_sz=None):
        def create_ds(make_empty=False):
            _sub_ds_cls=FirstLastExperienceSourceDataset if firstlast else ExperienceSourceDataset
            _sub_ds_cls=partial(_sub_ds_cls,env=env,n_envs=1,max_episode_step=0 if make_empty else max_steps,steps=skip_step)
            _ds_cls=AsyncDataExperienceSourceDataset if data_exp else AsyncGradExperienceSourceDataset
            _ds=_ds_cls(env,max_episode_step=0 if make_empty else max_steps,steps=skip_step,ds_cls=_sub_ds_cls,n_processes=n_processes,queue_sz=queue_sz)
            if display:_ds=DatasetDisplayWrapper(_ds,cols=cols,rows=rows,max_w=max_w)
            return _ds

#         dss=(create_ds(),create_ds() if add_valid else None)
        return cls.create(create_ds(),create_ds(not add_valid),bs=n_envs,num_workers=0)

# Cell
@safe_fit
def dqn_fitter(model:nn.Module,agent:BaseAgent,ds:ExperienceSourceDataset,data_queue:mp.JoinableQueue,
               pause_event:mp.Event,cancel_event:mp.Event,metric_queue:mp.JoinableQueue=None):
    dataset=ds()
    while not cancel_event.is_set():
        for xb,yb in dataset:
            data_queue.put(yb)
            if pause_event.is_set():cancel_event.wait(0.1)
            if cancel_event.is_set():break
        if metric_queue is not None:metric_queue.put(TotalRewards(np.mean(dataset.pop_total_r())))
        if cancel_event.is_set():break

@safe_fit
def dqn_grad_fitter(model:nn.Module,agent:BaseAgent,ds:ExperienceSourceDataset,grad_queue:mp.JoinableQueue,loss_queue:mp.JoinableQueue,
                    pause_event:mp.Event,cancel_event:mp.Event,metric_queue:mp.JoinableQueue=None):
    dataset=ds()
    while not cancel_event.is_set():
        for xb,yb in dataset:
            sys.stdout.flush()
            grad_queue.put(xb)
            loss_queue.put(0.5)
            if pause_event.is_set():cancel_event.wait(0.1)
            if cancel_event.is_set():break
        if metric_queue is not None:metric_queue.put(TotalRewards(np.mean(dataset.pop_total_r())))
        if cancel_event.is_set():break

@safe_fit
def buggy_dqn_fitter(model:nn.Module,agent:BaseAgent,ds:ExperienceSourceDataset,data_queue:mp.JoinableQueue,
                pause_event:mp.Event,cancel_event:mp.Event,metric_queue:mp.JoinableQueue=None):
    dataset=ds()
    while not cancel_event.is_set():
        for xb,yb in dataset:
            data_queue.put(yb)
            if pause_event.is_set():cancel_event.wait(0.1)
            if cancel_event.is_set():break
            raise Exception('Crashing on purpose')
        if cancel_event.is_set():break