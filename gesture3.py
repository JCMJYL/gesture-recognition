"""Three independent voltage traces to an A-Z template match."""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import ascii_uppercase

import numpy as np

from dtaidistance import dtw

CHANNELS = 'PRD'
NAMES = ('掌侧', '桡侧', '背侧')
SIZE = 96
RATE = 100
VERSION = 1
DISTANCE_LIMIT = {1: .38, 3: .37}
MAX_SEQUENCE_LETTERS = 48


class InputError(ValueError):
    pass


@dataclass
class Wave:
    time: np.ndarray
    voltage: np.ndarray


@dataclass
class Clip:
    start: float
    end: float
    shape: np.ndarray
    quality: float


@dataclass
class Model:
    shapes: np.ndarray
    labels: np.ndarray
    channels: np.ndarray
    source: str


def as_wave(rows):
    parsed = []
    for i, row in enumerate(rows, 1):
        if not any(x is not None and str(x).strip() for x in row):
            continue
        if len(row) < 2 or any(x is not None and str(x).strip() for x in row[2:]):
            raise InputError(f'第 {i} 行不是时间、电压两列')
        try:
            parsed.append((float(row[0]), float(row[1])))
        except (ValueError, TypeError):
            if not parsed and ('time' in str(row[0]).lower() or 'tims' in str(row[0]).lower() or '时间' in str(row[0])):
                continue
            raise InputError(f'第 {i} 行有空值或非数值') from None
    if len(parsed) < 15:
        raise InputError('采样点太少')
    t, v = np.asarray(parsed, dtype=float).T
    dt = np.diff(t)
    if not (np.isfinite(t).all() and np.isfinite(v).all() and np.all(dt > 0)):
        raise InputError('时间需严格递增，时间和电压均须为有限数值')
    if t[-1]-t[0] > 180 or np.median(dt) > .041 or dt.max() > .15:
        raise InputError('需要秒为单位的时间列、至少约 25 Hz 的采样率、无明显掉点且时长不超过 180 秒')
    return Wave(t-t[0], v)


def read_wave(path):
    path = Path(path)
    try:
        if path.suffix.lower() == '.xlsx':
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            try:
                return as_wave(wb.active.values)
            finally:
                wb.close()
        if path.suffix.lower() == '.csv':
            for encoding in ('utf-8-sig', 'gb18030'):
                try:
                    with path.open(encoding=encoding, newline='') as f:
                        return as_wave(csv.reader(f))
                except UnicodeDecodeError:
                    continue
            raise InputError('无法读取 CSV 编码')
        raise InputError('请选择两列 CSV 或 XLSX 文件')
    except InputError as exc:
        raise InputError(f'{path.name}：{exc}') from exc


def smooth(x, seconds):
    r = max(1, round(RATE*seconds/2))
    return np.convolve(np.pad(x, r, mode='edge'), np.ones(2*r+1)/(2*r+1), mode='valid')


def conditioned(wave):
    t, v = wave.time, wave.voltage
    tgrid = np.linspace(0, t[-1], round(t[-1]*RATE)+1)
    # Average locally before interpolation when the input is densely sampled.
    dt = np.median(np.diff(t))
    r = min(25, max(0, round(.008/dt)))
    dense = np.convolve(np.pad(v,r,mode='edge'),np.ones(2*r+1)/(2*r+1),mode='valid') if r else v
    x = np.interp(tgrid, t, dense)
    x = smooth(x, .030)
    x -= np.median(x[:max(4,min(14,len(x)//8))])
    env = np.sqrt(np.maximum(0,smooth(x*x, .11)))
    return tgrid, x, env


def quiet_boundaries(t, env):
    if t[-1] < 3:
        raise InputError('记录太短，无法拆分三次书写')
    ref = max(np.quantile(env,.96), 1e-10)
    centers = np.flatnonzero(env > .21*ref)
    if len(centers)<20:
        raise InputError('未检测到足够的动作')
    lo,hi = t[centers[[0,-1]]]
    if hi-lo < 2.5:
        raise InputError('未检测到三次分开的动作')
    width = min(1.7,.18*(hi-lo))
    quiet = smooth(env,.38)/ref
    cuts = [0]
    for f in (1/3, 2/3):
        target = lo+f*(hi-lo)
        options = np.flatnonzero((np.abs(t-target)<=width)&(t>=t[cuts[-1]]+.58)&(t<=t[-1]-.58))
        if not len(options):
            raise InputError('无法找到三次书写的分界')
        cost = quiet[options] + .20*(np.abs(t[options]-target)/width)**1.5
        cut = options[np.argmin(cost)]
        if quiet[cut] > .28:
            raise InputError('两次书写之间没有足够的静息间隔')
        cuts.append(cut)
    return cuts+[len(t)]


def clip_shape(t, x, env, multiple):
    high = float(np.quantile(env,.97))
    floor = float(np.quantile(env,.18))
    if high < 1e-7 or high < 2.35*floor:
        raise InputError('动作能量与噪声过于接近')
    active = np.flatnonzero(env > max(.16*high, 2.0*floor))
    if len(active)<12:
        raise InputError('未找到足够的有效动作')
    # Bridge short inter-stroke pauses, but do not attach isolated, weak resets.
    blocks = np.split(active,np.flatnonzero(np.diff(active)>round(.30*RATE))+1)
    strong = [b for b in blocks if len(b)>=8 and env[b].max() >= (.43 if multiple else .26)*high]
    if not strong:
        raise InputError('未检测到稳定动作段')
    begin = max(0,strong[0][0]-7)
    end = min(len(t)-1,strong[-1][-1]+8)
    return normalized_clip(t,x,begin,end,high/max(floor,1e-8))


def normalized_clip(t,x,begin,end,quality):
    if t[end]-t[begin] < .22:
        raise InputError('有效动作段太短')
    y = x[begin:end+1]
    z = np.interp(np.linspace(t[begin],t[end],SIZE),t[begin:end+1],y)
    z = (z-np.mean(z))/max(np.std(z),1e-8)
    return Clip(float(t[begin]),float(t[end]),np.ascontiguousarray(z),quality)


def preprocess(wave, repeats=1):
    if repeats not in (1,3):
        raise InputError('只支持单次或连续三次')
    t,x,e = conditioned(wave)
    bounds = quiet_boundaries(t,e) if repeats==3 else [0,len(t)]
    return [clip_shape(t[a:b],x[a:b],e[a:b],repeats==3) for a,b in zip(bounds,bounds[1:])]


def alternatives(t,x,env,a,b):
    segment = env[a:b]
    base = clip_shape(t[a:b],x[a:b],segment,True)
    ref = np.quantile(segment,.97)
    energy = np.maximum(segment**2-(.13*ref)**2,0)
    total = max(energy.sum(),1e-12)
    windows = set()
    for threshold in (.12,.21):
        active = np.flatnonzero(segment > max(threshold*ref, 1.4*np.quantile(segment,.16)))
        blocks = [block for block in np.split(active,np.flatnonzero(np.diff(active)>20)+1) if len(block)>=6]
        if not blocks:
            continue
        starts = {max(0,int(block[0])-7) for block in blocks[:3]}
        ends = {min(b-a-1,int(block[-1])+8) for block in blocks[-3:]}
        for left in starts:
            for right in ends:
                if right>left:
                    windows.add((left,right))
    start,end = (int(np.searchsorted(t[a:b],s)) for s in (base.start,base.end))
    for left in (start,max(0,start-29),min(b-a-1,start+33)):
        for right in (end,min(b-a-1,end+38),max(0,end-37)):
            if right>left:
                windows.add((left,right))
    out = []
    for left,right in sorted(windows):
        if t[a+right]-t[a+left]<.28:
            continue
        fraction = energy[left:right+1].sum()/total
        if fraction >= .40:
            out.append((normalized_clip(t,x,a+left,a+right,base.quality),.155*(1-fraction)))
    if not out:
        return [(base,0.)]
    return out


def search_windows(wave,repeats):
    if repeats==1:
        return [[(preprocess(wave)[0],0.)]]
    if repeats!=3:
        raise InputError('只支持单次或连续三次')
    t,x,env = conditioned(wave)
    bounds = quiet_boundaries(t,env)
    return [alternatives(t,x,env,a,b) for a,b in zip(bounds,bounds[1:])]


def reference_files(root):
    """Index user-labeled inputs; do not derive a letter from an unlabeled query."""
    root=Path(root)
    if not root.is_dir():
        raise InputError('请选择包含 A 至 Z 子文件夹的目录')
    entries={}
    missing=[]
    for letter in ascii_uppercase:
        found={ch:{} for ch in CHANNELS}
        for p in sorted((root/letter).glob('*')):
            if not p.is_file() or p.suffix.lower() not in ('.csv','.xlsx'):
                continue
            compact=re.fullmatch(rf'{letter}-([1-3])',p.stem,re.IGNORECASE)
            named=re.fullmatch(rf'{letter}_([PRD])(?:_(\d+))?',p.stem,re.IGNORECASE)
            if compact:
                ch=CHANNELS[int(compact[1])-1]
                index=None
            elif named:
                ch=named[1].upper()
                index=int(named[2]) if named[2] is not None else None
            else:
                continue
            if index in found[ch]:
                raise InputError(f'{letter} / {ch} 参考文件重复：{found[ch][index].name}、{p.name}')
            found[ch][index]=p
        for ch,files in found.items():
            if not files:
                missing.append(f'{letter}/{ch}')
                continue
            if None in files and len(files)>1:
                raise InputError(f'{letter} / {ch} 请只保留整段记录，或只保留编号的单次文件')
            entries[letter,ch]=files
    if missing:
        raise InputError(f'缺少 {len(missing)} 个字母/通道的参考信号：'+ '、'.join(missing[:12]) + ('…' if len(missing)>12 else ''))
    return entries


def reference_shapes(wave,repeats):
    if repeats==1:
        return [preprocess(wave)[0].shape]
    t,x,env=conditioned(wave)
    bounds=quiet_boundaries(t,env)
    groups=[alternatives(t,x,env,a,b) for a,b in zip(bounds,bounds[1:])]

    def pair_cost(first,second):
        return np.array([[dtw.distance_fast(a.shape,b.shape,window=15,penalty=.13)/np.sqrt(SIZE)
                          + .06*abs(np.log((a.end-a.start)/(b.end-b.start)))
                          for b,_ in second] for a,_ in first])

    # Repetitions of the same letter can guide training crops before a bank exists.
    d01=pair_cost(groups[0],groups[1])
    d02=pair_cost(groups[0],groups[2])
    d12=pair_cost(groups[1],groups[2])
    loss=(d01[:,:,None]+d02[:,None,:]+d12[None,:,:])/3
    penalties=[np.array([p for _,p in group]) for group in groups]
    loss+=penalties[0][:,None,None]+penalties[1][None,:,None]+penalties[2][None,None,:]
    indices=np.unravel_index(np.argmin(loss),loss.shape)
    return [group[i][0].shape for group,i in zip(groups,indices)]


def train(root, source='参考信号', recording_repeats=1):
    if recording_repeats not in (1,3):
        raise InputError('每路整段记录仅支持写一次或同字三次')
    labels, channels, shapes = [], [], []
    for (letter,ch),files in reference_files(root).items():
        for index,path in files.items():
            repeats=recording_repeats if index is None else 1
            try:
                samples=reference_shapes(read_wave(path),repeats)
            except InputError as exc:
                raise InputError(f'{letter} / {ch}：{path.name}：{exc}') from exc
            shapes.extend(samples)
            labels.extend([letter]*len(samples))
            channels.extend([ch]*len(samples))
    return Model(np.asarray(shapes),np.asarray(labels),np.asarray(channels),source)


def save_model(model, path):
    with Path(path).open('wb') as f:
        np.savez_compressed(f,version=VERSION,shapes=model.shapes,labels=model.labels,channels=model.channels,source=model.source)


def load_model(path):
    with np.load(path, allow_pickle=False) as d:
        if int(d['version']) != VERSION:
            raise InputError('模板版本不兼容，请重新训练')
        m = Model(d['shapes'],d['labels'],d['channels'],str(d['source']))
    if (m.shapes.shape != (len(m.labels),SIZE) or m.labels.shape != m.channels.shape
        or not np.isfinite(m.shapes).all() or any(not np.any((m.labels==l)&(m.channels==c)) for l in ascii_uppercase for c in CHANNELS)):
        raise InputError('模板库数据不完整或无效')
    return m


def match(model, waves, repeats=1, *, flexible_edges=False):
    if len(waves)!=3:
        raise InputError('需要按掌侧 P、桡侧 R、背侧 D 提供三路波形')
    selected, evidence = [], []
    for name,ch,wave in zip(NAMES,CHANNELS,waves):
        try:
            if flexible_edges and repeats==1:
                t,x,e=conditioned(wave)
                groups=[alternatives(t,x,e,0,len(t))]
            else:
                groups = search_windows(wave,repeats)
        except InputError as exc:
            raise InputError(f'{name} {ch}：{exc}') from exc
        use = model.channels==ch
        templates = model.shapes[use]
        labels = model.labels[use]
        groups_by_letter=[np.flatnonzero(labels==l) for l in ascii_uppercase]
        all_scores, picked = [], []
        for group in groups:
            distances = np.array([[dtw.distance_fast(s.shape,q,window=15,penalty=.13)/np.sqrt(SIZE)+cost
                                   for q in templates] for s,cost in group])
            scores=[]
            for indices in groups_by_letter:
                nearest=np.sort(distances[:,indices],axis=1)
                # With a larger bank, require support beyond one unusually close match.
                weights=(.66,.25,.09) if len(indices)>=6 else (.80,.20) if len(indices)>=2 else (1.,)
                scores.append(nearest[:,:len(weights)]@weights)
            scores=np.array(scores)
            best = np.argmin(scores,axis=1)
            all_scores.append(scores[np.arange(len(best)),best])
            picked.append([group[j][0] for j in best])
        selected.append(picked)
        # Each channel votes after pooling its OWN repeats; no cross-channel trial pairing.
        evidence.append(.70*np.median(all_scores,axis=0)+.30*np.mean(all_scores,axis=0))
    evidence = np.array(evidence)
    scores = np.mean(evidence,axis=0)
    rank = np.argsort(scores,kind='stable')
    first, second = rank[:2]
    gap = float(scores[second]-scores[first])
    support = int(np.sum(np.sum(evidence < evidence[:,first,None],axis=1)<3))
    reasons = []
    if scores[first]>DISTANCE_LIMIT[repeats]:
        reasons.append('与模板库的波形差异较大')
    if gap < max(.019,.075*float(scores[first])):
        reasons.append('前两名候选过于接近')
    votes = int(np.sum(np.argmin(evidence,axis=1)==first))
    disagreement = float(np.max(evidence[:,first]-evidence.min(axis=1)))
    if (votes<2 and not (votes==1 and support==3)) or disagreement > .17:
        reasons.append('不同通道的匹配结果不一致')
    def candidate(i):
        # A score of absolute waveform fit, not a posterior or an accuracy estimate.
        distance=float(scores[i])
        return dict(letter=ascii_uppercase[i],distance=distance,match_percent=float(100*np.exp(-.5*(distance/.26)**2)))
    return dict(letter=None if reasons else ascii_uppercase[first], reasons=reasons,
                candidates=[candidate(i) for i in rank[:3]],
                channel_best={c:ascii_uppercase[i] for c,i in zip(CHANNELS,np.argmin(evidence,axis=1))},
                margin=gap, supporting_channels=support, strongest_channel_disagreement=disagreement, source=model.source,
                windows={c:[[s[first].start,s[first].end] for s in groups] for c,groups in zip(CHANNELS,selected)})


def estimate_pause(gaps):
    """Separate longer quiet gaps from brief troughs in the same recording."""
    measured=np.sort(gaps[(gaps>=.065)&(gaps<=2.8)])
    if len(measured)==0:
        return .78
    if len(measured)>1:
        low,high=measured[:-1],measured[1:]
        allowed=(low<=.66)&(high>=.48)&(high/low>=1.35)
        # Do not favor gaps between very short troughs and moderate inter-stroke pauses.
        merit=np.log(high/low) + .45*np.minimum(high,.95)
        if allowed.any():
            j=np.argmax(np.where(allowed,merit,-np.inf))
            return float(np.clip(np.sqrt(low[j]*high[j]),.42,.90))
    long=measured[measured>=.58]
    return float(np.clip(.88*long[0],.48,.85)) if len(long) else .66


def spelling_windows(t, env, pause, fraction):
    baseline = np.median(env[:max(3, np.searchsorted(t,.20))])
    quiet = min(baseline, np.quantile(env,.18))
    threshold = max(3.1*quiet, fraction*np.quantile(env,.97), 1e-7)
    active = np.flatnonzero(env>threshold)
    if not len(active):
        return []
    gaps=np.diff(t[active])
    cutoff=estimate_pause(gaps) if pause is None else pause
    runs = np.split(active, np.flatnonzero(gaps >= cutoff)+1)
    bouts = [(float(t[r[0]]),float(t[r[-1]])) for r in runs
             if len(r)>=8 and t[r[-1]]-t[r[0]]>=.19 and np.max(env[r])>1.2*threshold]
    # Retain adjacent letters as distinct bouts, even if their templates are identical.
    windows=[]
    for i,(lo,hi) in enumerate(bouts):
        left = (bouts[i-1][1]+lo)/2 if i else 0.
        right = (hi+bouts[i+1][0])/2 if i+1<len(bouts) else float(t[-1])
        windows.append([max(left,lo-.20),min(right,hi+.26)])
    return windows


def split_spelling(wave, pause=None):
    """Find sequential bouts from each channel's time and activity alone."""
    t, _, env = conditioned(wave)
    return spelling_windows(t,env,pause,.045)


def recheck_windows(profiles, initial):
    """Keep only a one-boundary correction that survives adjacent thresholds."""
    counts = sorted(len(windows) for windows in initial.values())
    size = counts[1]
    if not 2<=size<=MAX_SEQUENCE_LETTERS or max(size-counts[0],counts[-1]-size)>1:
        return None
    revised = initial.copy()
    for ch,(t,env) in profiles.items():
        if len(initial[ch])==size:
            continue
        previous = None
        for fraction in (.060,.075,.090,.105,.120):
            current = spelling_windows(t,env,None,fraction)
            if len(current)==size and previous is not None and np.max(np.abs(np.array(current)-previous))<=.35:
                old=np.array(initial[ch])
                overlap=np.maximum(0,np.minimum(old[:,None,1],previous[None,:,1])-np.maximum(old[:,None,0],previous[None,:,0]))
                union=np.maximum(old[:,None,1],previous[None,:,1])-np.minimum(old[:,None,0],previous[None,:,0])
                # A rescue may affect a neighboring pair, not rearrange the whole trace.
                if np.count_nonzero(overlap/union>=.60)>=max(1,min(len(old),size)-1):
                    revised[ch]=previous.tolist()
                    break
            previous = np.array(current) if len(current)==size else None
        else:
            return None
    return revised


def decode_windows(model, waves, windows):
    positions=[]
    strong=True
    for index in range(len(windows['P'])):
        segments=[]
        for ch,w in zip(CHANNELS,waves):
            a,b=windows[ch][index]
            i=int(np.searchsorted(w.time,a))
            j=int(np.searchsorted(w.time,b,side='right'))
            segments.append(Wave(w.time[i:j]-w.time[i],w.voltage[i:j]))
        try:
            recognized=match(model,segments,1,flexible_edges=True)
            strong &= (recognized['letter'] is not None and recognized['margin']>=.023
                       and recognized['candidates'][0]['distance']<=.29 and recognized['strongest_channel_disagreement']<=.13)
        except InputError as exc:
            strong=False
            recognized=dict(letter=None,candidates=[],reasons=[str(exc)])
        positions.append(dict(index=index+1,letter=recognized['letter'],candidates=recognized['candidates'],reasons=recognized['reasons']))
    return positions,strong


def match_sequence(model, waves, pause=None):
    if len(waves)!=3:
        raise InputError('需要按掌侧 P、桡侧 R、背侧 D 提供三路波形')
    if pause is not None and not .45<=pause<=3:
        raise InputError('静息判定需在 0.45–3.00 秒之间')
    profiles={c:(t,env) for c,(t,_,env) in zip(CHANNELS,map(conditioned,waves))}
    windows={c:spelling_windows(t,env,pause,.045) for c,(t,env) in profiles.items()}
    initial_counts={c:len(v) for c,v in windows.items()}
    def result(text, positions, reasons, rechecked=False):
        return dict(text=text,positions=positions,counts={c:len(v) for c,v in windows.items()},
                    initial_counts=initial_counts,rechecked=rechecked,reasons=reasons,windows=windows,source=model.source)
    if not any(initial_counts.values()):
        return result(None,[],['三路均未检测到书写动作'])
    if len(set(initial_counts.values()))!=1:
        revised=recheck_windows(profiles,windows) if pause is None else None
        if revised is not None:
            positions,strong=decode_windows(model,waves,revised)
            if strong:
                windows=revised
                return result(''.join(p['letter'] for p in positions),positions,[],rechecked=True)
        details = '、'.join(f'{c} {n} 段' for c,n in initial_counts.items())
        return result(None,[],[f'各路检测段数不一致（{details}），复核后仍无法确认。请检查三路书写顺序和停顿。'])
    if initial_counts['P']>MAX_SEQUENCE_LETTERS:
        return result(None,[],[f'每段记录最多识别 {MAX_SEQUENCE_LETTERS} 个字母，请分批导入'])
    positions,_=decode_windows(model,waves,windows)
    return result(''.join(p['letter'] or '?' for p in positions),positions,[])


def main():
    parser = argparse.ArgumentParser(description='A-Z 压电三通道波形识别')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('train'); p.add_argument('dataset',type=Path)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--source',default='参考信号')
    p.add_argument('--recording-repeats',type=int,choices=(1,3),default=1)
    for action in ('predict','spell'):
        p = sub.add_parser(action); p.add_argument('--model',type=Path,required=True)
        for name in 'prd':
            p.add_argument('--'+name,type=Path,required=True)
        if action=='predict':
            p.add_argument('--repeats',type=int,choices=(1,3),default=1)
    args = parser.parse_args()
    try:
        if args.command=='train':
            if args.model.exists():
                raise InputError('模板文件已存在，请指定一个新文件名')
            model = train(args.dataset,args.source,args.recording_repeats)
            save_model(model,args.model)
            print(f'{len(model.labels)} 个模板，覆盖 26 字母 × 3 通道：{args.model}')
        else:
            paths = [getattr(args,c.lower()).resolve() for c in CHANNELS]
            if len(set(paths))!=3:
                raise InputError('请为三个通道分别选择文件')
            waves = [read_wave(path) for path in paths]
            model=load_model(args.model)
            out=match_sequence(model,waves) if args.command=='spell' else match(model,waves,args.repeats)
            print(json.dumps(out,ensure_ascii=False,indent=2))
    except (InputError,OSError,ValueError) as exc:
        parser.exit(2,f'{exc}\n')


if __name__=='__main__':
    main()
