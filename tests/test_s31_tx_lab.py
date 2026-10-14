"""The write-enabled research preflight must restore DC and never enable TX."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class S31TxLab(unittest.TestCase):
    def test_lab_is_excluded_from_release_matrix(self):
        script = ROOT / 'tools/build_firmware.py'
        matrix = json.loads(subprocess.check_output([sys.executable, str(script), '--matrix']))
        self.assertNotIn('esp32s31txlab', {p['id'] for p in matrix['include']})
        for args in [('--matrix', '--s31-tx-lab'),
                     ('--profile', 'esp32s3', '--s31-tx-lab', '--version', 'test')]:
            p = subprocess.run([sys.executable, str(script), *args], capture_output=True)
            self.assertNotEqual(p.returncode, 0)

    @unittest.skipUnless(shutil.which('cc'), 'Host C compiler unavailable')
    def test_roundtrip_restore_and_failures(self):
        header = ROOT / 'main/targets/esp32s31/burst/s31_tx_lab.h'
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#define S31_TX_LAB 1
static uint32_t dc, clock_reg, original;
static unsigned writes, depth, stalled, rx_calls, off_calls;
static int mode, dump_mux;
static char message[256];
static uint32_t read_reg(uint32_t address) {
    if (address==0x20100890u) return clock_reg;
    assert(address==0x20100c08u);
    if (mode==1 && writes==1) return dc^1u;
    return dc;
}
static void write_reg(uint32_t address,uint32_t value) {
    assert(address==0x20100c08u && depth==1 && stalled==1);
    assert(((value^original)&~0xfffffu)==0);
    writes++; dc=value;
    if(mode==2 && writes>1 && value==original)dc^=1u;
    if(mode==4 && writes==1)clock_reg|=0x30000u;
}
#define REG_READ(a) read_reg(a)
#define REG_WRITE(a,v) write_reg(a,v)
static void prepare_rx(void) {rx_calls++;}
static void phy_pbus_xpd_tx_off(void) {off_calls++;}
static void phy_set_txclk_en(unsigned value) {
    assert(value==0); if(mode!=3)clock_reg&=~0x30000u;
}
static void esp_ipc_isr_stall_other_cpu(void) {assert(!stalled);stalled=1;}
static void esp_ipc_isr_release_other_cpu(void) {assert(stalled && !depth);stalled=0;}
#define taskENTER_CRITICAL(p) do {(void)(p);assert(!depth && stalled);depth=1;} while(0)
#define taskEXIT_CRITICAL(p) do {(void)(p);assert(depth==1);depth=0;} while(0)
static void reply(const char *fmt,...) {
    va_list args;va_start(args,fmt);vsnprintf(message,sizeof message,fmt,args);va_end(args);
}
#include "s31_tx_lab.h"
static void reset(int failure) {
    original=dc=0x0e0abcdeu;clock_reg=0x0080c050u;
    writes=depth=stalled=rx_calls=off_calls=0;mode=failure;s31_tx_lab_fault=false;
}
int main(void) {
    reset(0);
    assert(s31_tx_lab_command("TXLAB?"));assert(writes==0);
    assert(!s31_tx_lab_command("TXSTART"));assert(writes==0);
    assert(!s31_tx_lab_command("TXLABCHECK extra"));assert(writes==0);
    assert(s31_tx_lab_command("TXLABCHECK"));
    assert(strstr(message,"ok=1 tested=4 mismatches=0 restored=1 clock_off=1"));
    assert(dc==original && writes==5 && !depth && !stalled);
    assert(rx_calls==2 && off_calls==2 && !s31_tx_lab_fault);
    reset(1);assert(s31_tx_lab_command("TXLABCHECK"));
    assert(strstr(message,"ok=0 tested=1 mismatches=1 restored=1"));
    assert(writes==2 && dc==original && !(clock_reg&0x30000u));
    reset(2);assert(s31_tx_lab_command("TXLABCHECK"));
    assert(strstr(message,"restored=0") && s31_tx_lab_fault);
    unsigned previous=writes;
    assert(s31_tx_lab_command("TXLABCHECK"));assert(writes==previous);
    assert(strstr(message,"ERR txlab_fault_reset_required"));
    reset(3);clock_reg|=0x30000u;assert(s31_tx_lab_command("TXLABCHECK"));
    assert(writes==0 && dc==original && s31_tx_lab_fault);
    reset(4);assert(s31_tx_lab_command("TXLABCHECK"));
    assert(writes==2 && dc==original && !(clock_reg&0x30000u));
    assert(s31_tx_lab_fault && strstr(message,"clock_off=0"));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'lab.c'
            binary = Path(temp) / 'lab'
            source.write_text(code, encoding='utf-8')
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(header.parent), str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
