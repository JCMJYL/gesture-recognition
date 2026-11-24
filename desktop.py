"""Local desktop front end for gesture3."""

from concurrent.futures import ThreadPoolExecutor
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from gesture3 import CHANNELS, DEFAULT_MODEL, NAMES, load_model, match, match_sequence, read_wave

BG, INK, MUTED, LINE = '#f3f5f6', '#162727', '#536268', '#d9e1e3'
ACCENT = '#146654'
COLORS = ('#b74041', '#6a58a8', '#2063ab')
FILENAMES = [('时序文件', '*.csv *.xlsx'), ('CSV', '*.csv'), ('Excel', '*.xlsx')]


def candidate_label(candidate):
    return f"{candidate['letter']} {candidate['match_percent']:.0f}%"


class App:
    def __init__(self, root, model):
        self.root, self.model = root, model
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.paths, self.waves = [None]*3,[None]*3
        self.pending = None
        self.result = None
        self.mode = tk.IntVar(value=0)
        self.answer = tk.StringVar(value='—')
        self.status = tk.StringVar(value='等待导入')
        root.title('A–Z 压电手势识别')
        root.geometry('1100x735')
        root.minsize(930, 650)
        root.configure(bg=BG)
        root.option_add('*Font', ('Microsoft YaHei UI',10))
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TButton',padding=(10,5))
        style.configure('Primary.TButton',background=ACCENT,foreground='white',borderwidth=0,padding=(12,8))
        style.map('Primary.TButton',background=[('disabled','#dbe4e1'),('active','#0d5142')],
                  foreground=[('disabled','#4c6560')])
        style.configure('Treeview',rowheight=27,fieldbackground='white',borderwidth=0)
        style.configure('Treeview.Heading',font=('Microsoft YaHei UI',9),background='#edf2f3',foreground=INK)
        header = tk.Frame(root,bg=BG)
        header.pack(fill='x',padx=24,pady=(16,12))
        tk.Label(header,text='压电手势识别',font=('Microsoft YaHei UI',19,'bold'),bg=BG,fg=INK).pack(side='left')

        main = tk.Frame(root,bg=BG)
        main.pack(fill='both',expand=True,padx=24,pady=(0,19))
        main.grid_columnconfigure(1,weight=1)
        main.grid_rowconfigure(0,weight=1)
        left = tk.Frame(main,bg='white',highlightbackground=LINE,highlightthickness=1)
        left.grid(row=0,column=0,sticky='ns',padx=(0,16))
        right = tk.Frame(main,bg='white',highlightbackground=LINE,highlightthickness=1)
        right.grid(row=0,column=1,sticky='nsew')

        def heading(parent,text):
            tk.Label(parent,text=text,bg='white',fg=INK,font=('Microsoft YaHei UI',11,'bold')).pack(anchor='w',padx=15,pady=(11,3))

        heading(left,'通道文件')
        self.file_labels, self.selectors = [],[]
        for i,(ch,name,col) in enumerate(zip(CHANNELS,NAMES,COLORS)):
            row = tk.Frame(left,bg='white')
            row.pack(fill='x',padx=15,pady=3)
            tk.Label(row,text=f'{ch}  {name}',width=8,anchor='w',bg='white',fg=col).pack(side='left')
            field = tk.Label(row,text='未导入',width=16,anchor='w',bg='white',fg=MUTED)
            field.pack(side='left',padx=(0,8))
            self.file_labels.append(field)
            button = ttk.Button(row,text='选择',width=6,command=lambda index=i:self.choose(index))
            button.pack(side='right')
            self.selectors.append(button)
        mode = tk.Frame(left,bg='white')
        mode.pack(fill='x',padx=15,pady=(7,5))
        for val,name in [(0,'连续拼写'),(1,'单字母'),(3,'同字三次')]:
            button = tk.Radiobutton(mode,text=name,variable=self.mode,value=val,bg='white',fg=INK,
                                    activebackground='white',highlightthickness=0,command=self.reset_result)
            button.pack(side='left',padx=(0,11))
            self.selectors.append(button)
        controls = tk.Frame(left,bg='white')
        controls.pack(fill='x',padx=15,pady=(6,7))
        self.go = ttk.Button(controls,text='识别',style='Primary.TButton',command=self.run,state='disabled')
        self.go.pack(fill='x')
        ttk.Separator(left).pack(fill='x',padx=15,pady=(7,0))
        heading(left,'识别结果')
        answer_row=tk.Frame(left,bg='white')
        answer_row.pack(fill='x',padx=15,pady=(2,4))
        self.answer_field=tk.Entry(answer_row,textvariable=self.answer,font=('Consolas',24,'bold'),width=10,
                                   relief='flat',readonlybackground='#edf5f2',fg=ACCENT,state='readonly')
        self.answer_field.pack(side='left',fill='x',expand=True,ipady=3,padx=(0,7))
        self.copy_button=ttk.Button(answer_row,text='复制',width=5,command=self.copy,state='disabled')
        self.copy_button.pack(side='right')
        tk.Label(left,textvariable=self.status,bg='white',fg=MUTED,wraplength=292,justify='left').pack(anchor='w',padx=15,pady=(0,8))
        table_frame=tk.Frame(left,bg='white')
        table_frame.pack(fill='x',padx=15)
        self.table = ttk.Treeview(table_frame,columns=('position','letter','details'),show='headings',height=5,selectmode='browse')
        self.table.heading('position',text='位置'); self.table.heading('letter',text='识别结果'); self.table.heading('details',text='备选匹配度')
        self.table.column('position',width=47,anchor='center',stretch=False)
        self.table.column('letter',width=82,anchor='center')
        self.table.column('details',width=135,anchor='center')
        scroll = ttk.Scrollbar(table_frame,orient='vertical',command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left',fill='x',expand=True)
        scroll.pack(side='right',fill='y')
        self.table.bind('<<TreeviewSelect>>',self.inspect_position)
        self.votes = tk.Label(left,text='',bg='white',fg=MUTED,font=('Microsoft YaHei UI',9),wraplength=280,justify='left')
        self.votes.pack(anchor='w',padx=15,pady=(8,4))

        heading(right,'电压时域信号 (V)')
        self.canvas = tk.Canvas(right,bg='white',highlightthickness=0)
        self.canvas.pack(fill='both',expand=True,padx=13,pady=(3,10))
        self.canvas.bind('<Configure>',lambda _event:self.draw())
        self.reset_result()

    def choose(self,i):
        name = filedialog.askopenfilename(title=f'{CHANNELS[i]} {NAMES[i]}',filetypes=FILENAMES,parent=self.root)
        if not name:
            return
        self.set_files({i:Path(name)})

    def copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.answer.get())
        self.status.set('已复制结果')

    def set_files(self, paths):
        try:
            assigned = [paths.get(i,p) for i,p in enumerate(self.paths)]
            chosen = [p.resolve() for p in assigned if p is not None]
            if len(chosen)!=len(set(chosen)):
                raise ValueError('请为三个通道分别选择文件')
            waves = {i:read_wave(p) for i,p in paths.items()}
        except Exception as exc:
            messagebox.showerror('无法导入',str(exc),parent=self.root)
            return False
        for i, p in paths.items():
            self.waves[i],self.paths[i] = waves[i],p
            self.file_labels[i].configure(text=p.name if len(p.name)<=16 else p.name[:12]+'…')
        self.reset_result()
        return True

    def reset_result(self):
        self.result = None
        self.answer.set('—')
        self.answer_field.configure(fg=ACCENT)
        self.copy_button.configure(state='disabled')
        spelling=self.mode.get()==0
        self.table.configure(displaycolumns=('position','letter','details') if spelling else ('letter','details'))
        self.table.heading('letter',text='识别结果' if spelling else '候选字母')
        self.table.heading('details',text='备选匹配度' if spelling else '匹配度')
        self.table.delete(*self.table.get_children())
        self.votes.configure(text='')
        ready = all(w is not None for w in self.waves)
        self.status.set('待识别' if ready else '等待导入')
        self.go.configure(state='normal' if ready else 'disabled')
        self.draw()

    def run(self):
        if self.pending is not None or not all(w is not None for w in self.waves):
            return
        mode=self.mode.get()
        self.reset_result()
        self.status.set('匹配中…')
        for b in [*self.selectors,self.go]: b.configure(state='disabled')
        waves=tuple(self.waves)
        self.pending = self.pool.submit(match_sequence,self.model,waves) if mode==0 else self.pool.submit(match,self.model,waves,mode)
        self.root.after(30,self.poll)

    def poll(self):
        if not self.pending.done():
            self.root.after(30,self.poll)
            return
        for b in [*self.selectors,self.go]: b.configure(state='normal')
        pending,self.pending = self.pending,None
        try:
            result = pending.result()
        except Exception as exc:
            self.answer.set('无法确定')
            self.answer_field.configure(fg='#8a3c29')
            self.status.set(str(exc))
            return
        self.result = result
        if 'positions' in result:
            self.show_spelling(result)
            return
        label = result['letter']
        self.answer.set(label or '无法确定')
        self.answer_field.configure(fg=ACCENT if label else '#8a3c29')
        self.copy_button.configure(state='normal' if label else 'disabled')
        self.status.set('识别完成' if label else '；'.join(result['reasons']))
        for candidate in result['candidates']:
            self.table.insert('', 'end', values=('',candidate['letter'],f"{candidate['match_percent']:.0f}%"))
        self.votes.configure(text='    '.join(f'{c}：{letter}' for c,letter in result['channel_best'].items()))
        self.draw()

    def show_spelling(self,result):
        word=result['text']
        self.answer.set(word or '无法确定')
        self.answer_field.xview_moveto(0)
        self.answer_field.configure(fg=ACCENT if word and '?' not in word else '#8a3c29')
        self.copy_button.configure(state='normal' if word else 'disabled')
        if not word:
            self.status.set('；'.join(result['reasons']))
        else:
            unknown=word.count('?')
            message=f'{len(word)} 个位置，{unknown} 个待确认' if unknown else f'已识别 {len(word)} 个字母'
            self.status.set(message+(' · 分段已复核' if result.get('rechecked') else ''))
            for p in result['positions']:
                candidates=p['candidates']
                primary=candidate_label(candidates[0]) if p['letter'] else '待确认'
                choices=' / '.join(candidate_label(c) for c in (candidates[1:] if p['letter'] else candidates[:2]))
                self.table.insert('', 'end', iid=str(p['index']),values=(p['index'],primary,choices or '—'))
        self.draw()

    def inspect_position(self,_event):
        selection=self.table.selection()
        if self.result and self.result.get('positions') and selection:
            p=self.result['positions'][int(selection[0])-1]
            matches=' / '.join(candidate_label(c) for c in p['candidates'])
            notice='；'.join(p['reasons'])
            message='\n'.join(s for s in (matches,notice) if s)
            self.votes.configure(text=f"第 {p['index']} 位：{message}")
            self.draw()

    def draw(self):
        cv = self.canvas
        cv.delete('all')
        w,h = cv.winfo_width(),cv.winfo_height()
        if w<130 or h<150:
            return
        xmax = max((float(s.time[-1]) for s in self.waves if s is not None),default=1.)
        ymax = max((float(np.max(abs(s.voltage))) for s in self.waves if s is not None),default=1.)
        ymax = max(.02,ymax*1.08)
        x0,x1 = 64,w-24
        band = (h-42)/3
        for i,(ch,name,color,s) in enumerate(zip(CHANNELS,NAMES,COLORS,self.waves)):
            top,bottom = 24+i*band, (i+1)*band-8
            y0 = (top+bottom)/2
            cv.create_text(x0,top-17,text=f'{ch}  {name}',anchor='nw',fill=color,font=('Microsoft YaHei UI',9,'bold'))
            xp = lambda ts:x0+(x1-x0)*ts/xmax
            positions=self.result.get('positions',[]) if self.result else []
            windows=self.result['windows'][ch] if self.result else []
            selected=self.table.selection()
            for idx,(a,b) in enumerate(windows,1):
                color_fill='#cbe6da' if positions and str(idx) in selected else ('#e8f3ee' if idx%2 else '#e7eef7')
                cv.create_rectangle(xp(a),top,xp(b),bottom,fill=color_fill,outline='')
                if positions and xp(b)-xp(a)>24:
                    label=positions[idx-1]['letter'] or '?'
                    cv.create_text((xp(a)+xp(b))/2,top+6,text=label,anchor='n',fill=INK,font=('Consolas',10,'bold'))
            cv.create_rectangle(x0,top,x1,bottom,outline=LINE)
            cv.create_line(x0,y0,x1,y0,fill=LINE)
            for y,v in [(top,ymax),(y0,0),(bottom,-ymax)]:
                cv.create_text(x0-6,y,text=f'{v:.2f}' if v else '0',anchor='e',fill=MUTED,font=('Microsoft YaHei UI',8))
            if s is not None:
                t = s.time
                values = s.voltage
                step = max(1,len(t)//1800)
                xx = xp(t[::step]); yy = y0-(bottom-top)*values[::step]/(2*ymax)
                cv.create_line(*np.column_stack((xx,yy)).ravel(),fill=color,width=1.3)
            if i==2:
                for frac in (0,.25,.5,.75,1):
                    cv.create_text(x0+(x1-x0)*frac,bottom+7,text=f'{xmax*frac:.1f}',anchor='n',fill=MUTED,font=('Microsoft YaHei UI',8))
                cv.create_text((x0+x1)/2,bottom+23,text='时间 (s)',anchor='n',fill=MUTED,font=('Microsoft YaHei UI',9))

    def close(self):
        self.pool.shutdown(wait=False,cancel_futures=True)
        self.root.destroy()


if __name__=='__main__':
    parser = argparse.ArgumentParser(description='A-Z 压电手势识别桌面程序')
    parser.add_argument('--model',type=Path,default=DEFAULT_MODEL)
    args = parser.parse_args()
    root = tk.Tk()
    try:
        model = load_model(args.model)
    except Exception as exc:
        root.withdraw()
        messagebox.showerror('模板不可用',str(exc),parent=root)
        root.destroy()
    else:
        App(root,model)
        root.mainloop()
