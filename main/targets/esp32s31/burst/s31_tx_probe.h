/* ESP32-S31 TX research, stage 1: strictly read-only diagnostics.
 *
 * Only read MMIO already used by the existing S31 RX implementation.
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
        reply("TXCAPS 1 status=research_only dac=unverified burst=0 stream=0 soapy=0 rf=disabled\n");
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
