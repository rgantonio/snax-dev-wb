#!/usr/bin/env python3

# Copyright 2021 ETH Zurich and University of Bologna.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# This script parses the traces generated by Snitch and creates a JSON file
# that can be visualized by
# [Trace-Viewer](https://github.com/catapult-project/catapult/tree/master/tracing)
# In Chrome, open `about:tracing` and load the JSON file to view it.
#
# This script is inspired by https://github.com/SalvatoreDiGirolamo/tracevis
# Author: Noah Huetter <huettern@student.ethz.ch>
#         Samuel Riedel <sriedel@iis.ee.ethz.ch>

import re
import os
import sys
from functools import lru_cache
import argparse

has_progressbar = True
try:
    import progressbar
except ImportError as e:
    # Do not use progressbar
    print(f'{e} --> No progress bar will be shown.', file=sys.stderr)
    has_progressbar = False


# line format:
# Snitch RTL simulation:
# 101000 82      M         0x00001000 csrr    a0, mhartid     #; comment
# time   cycle   priv_lvl  pc         insn
# MemPool RTL simulation:
# 101000 82      0x00001000 csrr    a0, mhartid     #; comment
# time   cycle   pc         insn
# Banshee traces:
# 00000432 00000206 0005     800101e0  x15:00000064 x15=00000065 # addi    a5, a5, 1
# cycle    instret  hard_id  pc        register                    insn

# regex matches to groups
# 0 -> time
# 1 -> cycle
# 2 -> privilege level (RTL) / hartid (banshee)
# 3 -> pc (hex with 0x prefix)
# 4 -> instruction
# 5 -> args (RTL) / empty (banshee)
# 6 -> comment (RTL) / instruction arguments (banshee)
RTL_REGEX = r' *(\d+) +(\d+) +([3M1S0U]?) *(0x[0-9a-f]+) ([.\w]+) +(.+)#; (.*)'
BANSHEE_REGEX = r' *(\d+) (\d+) (\d+) ([0-9a-f]+) *.+ +.+# ([\w\.]*)( +)(.*)'

# regex matches a line of instruction retired by the accelerator
# 0 -> time
# 1 -> cycle
# 2 -> privilege level
# 3 -> comment
ACC_LINE_REGEX = r' *(\d+) +(\d+) +([3M1S0U]?) *#; (.*)'

buf = []


@lru_cache(maxsize=1024)
def addr2line_cache(addr):
    cmd = f'{addr2line} -e {elf} -f -a -i {addr:x}'
    return os.popen(cmd).read().split('\n')


def flush(buf, hartid):
    global output_file
    # get function names
    pcs = [x[3] for x in buf]
    a2ls = []

    if cache:
        for addr in pcs:
            a2ls += addr2line_cache(int(addr, base=16))[:-1]
    else:
        a2ls = os.popen(
            f'{addr2line} -e {elf} -f -a -i {" ".join(pcs)}').read().split('\n')[:-1]

    for i in range(len(buf)-1):
        (time, cyc, priv, pc, instr, args, cmt) = buf.pop(0)

        if use_time:
            next_time = int(buf[0][0])
            time = int(time)
        else:
            next_time = int(buf[0][1])
            time = int(cyc)

        # Have lookahead time to this instruction?
        next_time = lah[time] if time in lah else next_time

        # print(f'time "{time}", cyc "{cyc}", priv "{priv}", pc "{pc}"'
        #       f', instr "{instr}", args "{args}"', file=sys.stderr)

        [pc, func, file] = a2ls.pop(0), a2ls.pop(0), a2ls.pop(0)

        # check for more output of a2l
        inlined = ''
        while not a2ls[0].startswith('0x'):
            inlined += '(inlined by) ' + a2ls.pop(0)
        # print(f'pc "{pc}", func "{func}", file "{file}"')

        # assemble values for json
        # Doc: https://docs.google.com/document/d/1CvAClvFfyA5R-PhYUmn5OOQtYMH4h6I0nSsKchNAySU/preview
        # The name of the event, as displayed in Trace Viewer
        name = instr
        # The event categories. This is a comma separated list of categories for the event.
        # The categories can be used to hide events in the Trace Viewer UI.
        cat = 'instr'
        # The tracing clock timestamp of the event. The timestamps are provided at microsecond granularity.
        ts = time
        # There is an extra parameter dur to specify the tracing clock duration of complete events in microseconds.
        duration = next_time - time

        if banshee:
            # Banshee stores all traces in a single file
            hartid = priv
            # In Banshee, each instruction takes one cycle
            duration = 1

        pid = elf+':hartid'+str(hartid)
        funcname = func

        # args
        arg_pc = pc
        arg_instr = instr
        arg_args = args
        arg_cycles = cyc
        arg_coords = file
        arg_inlined = inlined

        output_file.write((
            f'{{"name": "{name}", "cat": "{cat}", "ph": "X", '
            f'"ts": {ts}, "dur": {duration}, "pid": "{pid}", '
            f'"tid": "{funcname}", "args": {{"pc": "{arg_pc}", '
            f'"instr": "{arg_instr} {arg_args}", "time": "{arg_cycles}", '
            f'"Origin": "{arg_coords}", "inline": "{arg_inlined}"'
            f'}}}},\n'))


def parse_line(line, hartid):
    global last_time, last_cyc
    # print(line)
    match = re_line.match(line)
    if match:
        (time, cyc, priv, pc, instr, args, cmt) = tuple(
            [match.group(i+1).strip() for i in range(re_line.groups)])
        buf.append((time, cyc, priv, pc, instr, args, cmt))
        last_time, last_cyc = time, cyc

    if len(buf) > 10:
        flush(buf, hartid)
    return 0


# Argument parsing
parser = argparse.ArgumentParser('tracevis', allow_abbrev=True)
parser.add_argument(
    'elf',
    metavar='<elf>',
    help='The binary executed to generate the traces',


)
parser.add_argument(
    'traces',
    metavar='<trace>',
    nargs='+',
    help='Snitch traces to visualize')
parser.add_argument(
    '-o',
    '--output',
    metavar='<json>',
    nargs='?',
    default='chrome.json',
    help='Output JSON file')
parser.add_argument(
    '--addr2line',
    metavar='<path>',
    nargs='?',
    default='addr2line',
    help='`addr2line` binary to use for parsing')
parser.add_argument(
    '-t',
    '--time',
    action='store_true',
    help='Use the traces time instead of cycles')
parser.add_argument(
    '-b',
    '--banshee',
    action='store_true',
    help='Parse Banshee traces')
parser.add_argument(
    '--no-cache',
    action='store_true',
    help='Disable addr2line caching (slow but might give better traces in some cases)')
parser.add_argument(
    '-s',
    '--start',
    metavar='<line>',
    nargs='?',
    type=int,
    default=0,
    help='First line to parse')
parser.add_argument(
    '-e',
    '--end',
    metavar='<line>',
    nargs='?',
    type=int,
    default=-1,
    help='Last line to parse')

args = parser.parse_args()

elf = args.elf
traces = args.traces
output = args.output
use_time = args.time
banshee = args.banshee
addr2line = args.addr2line
cache = not args.no_cache

print('elf:', elf, file=sys.stderr)
print('traces:', traces, file=sys.stderr)
print('output:', output, file=sys.stderr)
print('addr2line:', addr2line, file=sys.stderr)
print('cache:', cache, file=sys.stderr)

# Compile regex
if banshee:
    re_line = re.compile(BANSHEE_REGEX)
else:
    re_line = re.compile(RTL_REGEX)

re_acc_line = re.compile(ACC_LINE_REGEX)


def offload_lookahead(lines):
    # dict mapping time stamp of retired instruction to time stamp of
    # accelerator complete
    lah = {}
    searches = []
    re_load = re.compile(r'([a-z]*[0-9]*|zero) *<~~ Word')

    for line in lines:
        match = re_line.match(line)
        if match:
            (time, cyc, priv, pc, instr, args, cmt) = tuple(
                [match.group(i+1).strip() for i in range(re_line.groups)])
            time = int(time) if use_time else int(cyc)

            # register searchers
            if '<~~ Word' in cmt:
                if re_load.search(cmt):
                    dst_reg = re_load.search(cmt).group(1)
                    pat = f'(lsu) {dst_reg}  <--'
                    searches.append({'pat': pat, 'start': time})
                else:
                    print(f'unsupported load lah: {cmt}')

        # If this line is an acc-only line, get the data
        if not match:
            match = re_acc_line.match(line)
            if match:
                (time, cyc, priv, cmt) = tuple(
                    [match.group(i+1).strip() for i in range(re_acc_line.groups)])

        time = int(time) if use_time else int(cyc)

        # Check for any open searches
        removes = []
        for s in searches:
            if s['pat'] in cmt:
                lah[s['start']] = time
                removes.append(s)
        [searches.remove(r) for r in removes]

    # for l in lah:
    #     print(f'{l} -> {lah[l]}')
    return lah


lah = {}

with open(output, 'w') as output_file:
    # JSON header
    output_file.write('{"traceEvents": [\n')

    for filename in traces:
        hartid = 0
        parsed_nums = re.findall(r'\d+', filename)
        hartid = int(parsed_nums[-1]) if len(parsed_nums) else hartid+1
        fails = lines = 0
        last_time = last_cyc = 0

        print(
            f'parsing hartid {hartid} with trace {filename}', file=sys.stderr)
        tot_lines = len(open(filename).readlines())
        with open(filename) as f:
            all_lines = f.readlines()[args.start:args.end]
            # offload lookahead
            if not banshee:
                lah = offload_lookahead(all_lines)
            if has_progressbar:
                for lino, line in progressbar.progressbar(
                        enumerate(all_lines),
                        max_value=tot_lines):
                    fails += parse_line(line, hartid)
                    lines += 1
            else:
                for lino, line in enumerate(
                        all_lines):
                    fails += parse_line(line, hartid)
                    lines += 1
            flush(buf, hartid)
            print(f' parsed {lines-fails} of {lines} lines', file=sys.stderr)

    # JSON footer
    output_file.write(r'{}]}''\n')
