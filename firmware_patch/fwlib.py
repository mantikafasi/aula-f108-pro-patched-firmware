import struct, collections, re
from capstone import *
import os; R=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"stock_res4000.bin"),"rb").read()
END=0x19f2c
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB); md.detail=True
def u32(a): return struct.unpack_from('<I',R,a)[0]
def sweep():
    ins=[]; a=0x100
    while a<END:
        got=False
        for i in md.disasm(R[a:a+4],a,1):
            ins.append(i); a+=i.size; got=True
        if not got: a+=2
    return ins
INS=sweep()
BYADDR={i.address:i for i in INS}
def litval(i):
    # ldr rX,[pc,#imm]
    m=re.fullmatch(r'r\d+, \[pc, #(0x[0-9a-f]+|\d+)\]',i.op_str) if i.mnemonic=='ldr' else None
    if m:
        imm=int(m.group(1),0)
        addr=((i.address+4)&~3)+imm
        if addr+4<=len(R): return u32(addr)
    return None
# function starts: push {..lr}
FUNCS=sorted(i.address for i in INS if i.mnemonic=='push' and 'lr' in i.op_str)
def func_of(a):
    import bisect
    k=bisect.bisect_right(FUNCS,a)-1
    return FUNCS[k] if k>=0 else None
def dis(a,b):
    for i in INS:
        if a<=i.address<b:
            v=litval(i)
            print('%05x: %-6s %s%s'%(i.address,i.mnemonic,i.op_str,('   ; =0x%x'%v) if v is not None else ''))

CALLERS=collections.defaultdict(list); CALLEES=collections.defaultdict(set)
for _i in INS:
    if _i.mnemonic=='bl':
        _t=int(_i.op_str.lstrip('#'),0); CALLERS[_t].append(_i.address); CALLEES[func_of(_i.address)].add(_t)
def callers(t): return sorted({func_of(a) for a in CALLERS.get(t,[])})
