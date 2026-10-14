/* ESP32-S31 TX research, stage 1: strictly read-only diagnostics.
 *
 * Read known RX MMIO and TX controls accessed by the S31 revision-0 ROM.
 * The ROM ELF entry trampolines and code were compared with board ROM bytes.
 * None of these registers proves that the DAC has an arbitrary-I/Q input.
 * Do not copy ESP32-S3 register values or enable an RF TX path here.
 */
#ifndef ESP_SDR_S31_TX_PROBE_H
#define ESP_SDR_S31_TX_PROBE_H
#include <stdint.h>
#include <string.h>
#include <inttypes.h>

static bool s31_tx_probe_command(const char *line)
{
    if (strcmp(line, "TXCAPS?") == 0) {
        reply("TXCAPS 1 status=research_only dac=unverified burst=0 stream=0 soapy=0 rf=disabled pathprobe=1\n");
        return true;
    }
    if (strcmp(line, "TXPATH?") == 0) {
        /* Names describe the matching S31 ROM routines, not a playback map.
         * All eight registers are read-modify-written by those routines.
         * Snapshot only: never call a TX function or change any control bit. */
        uint32_t rate = REG_READ(0x20100448u);  /* phy_dac_rate_set */
        uint32_t clock = REG_READ(0x20100890u); /* phy_set_txclk_en */
        uint32_t ctrl = REG_READ(0x2010040cu);  /* phy_start/stop_tx_tone */
        uint32_t tone_a = REG_READ(0x2010041cu);
        uint32_t tone_b = REG_READ(0x20100420u);
        uint32_t scale = REG_READ(0x20100c04u); /* phy_dac_scale_set */
        uint32_t filter = REG_READ(0x20107440u); /* phy_bbtx_outfilter */
        uint32_t dc = REG_READ(0x20100c08u); /* phy_fe_reg_update / set_txdc_dig */
        reply("TXPATH 1 passive dac_rate=%08" PRIx32 " tx_clock=%08" PRIx32
              " tone_ctrl=%08" PRIx32 " tone_a=%08" PRIx32
              " tone_b=%08" PRIx32 " dac_scale=%08" PRIx32
              " tx_filter=%08" PRIx32 " tx_dc=%08" PRIx32 "\n",
              rate, clock, ctrl, tone_a, tone_b, scale, filter, dc);
        return true;
    }
    if (strcmp(line, "TXPROBE?") == 0) {
        /* Snapshot RX dump/PHY state only. No register writes and no retune.
         * The PHY could still change values asynchronously during reads. */
        uint32_t ctrl = REG_READ(DUMP_CTRL);
        uint32_t mode = REG_READ(DUMP_MODE);
        uint32_t mac = REG_READ(DUMP_MAC);
        uint32_t gain = REG_READ(RX_GAIN);
        reply("TXPROBE 1 passive rx_dump_ctrl=%08" PRIx32
              " rx_dump_mode=%08" PRIx32 " rx_dump_mac=%08" PRIx32
              " rx_gain=%08" PRIx32 "\n", ctrl, mode, mac, gain);
        return true;
    }
    /* Explicitly fail closed; unknown commands cannot energize the RF path. */
    if (line[0] == 'T' && line[1] == 'X') {
        reply("ERR tx_unverified\n");
        return true;
    }
    return false;
}
#endif
