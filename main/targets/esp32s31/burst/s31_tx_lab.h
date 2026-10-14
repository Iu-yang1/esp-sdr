/* Opt-in S31 register preflight. This does not start DAC/RF playback.
 * The two ten-bit fields are derived from S31 set_txdc_dig, not S3 packing.
 * RF off and TX clock off are prerequisites; every write is bounded and the
 * original DC register is restored before returning to the RX command loop.
 */
#ifndef ESP_SDR_S31_TX_LAB_H
#define ESP_SDR_S31_TX_LAB_H
#ifndef S31_TX_LAB
#error "TX lab must only be compiled in the separate research profile"
#endif
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <inttypes.h>

#define S31_TX_LAB_DC_REG 0x20100c08u
#define S31_TX_LAB_CLK_REG 0x20100890u
#define S31_TX_LAB_FIELDS 0x000fffffu
#define S31_TX_LAB_CLK_MASK 0x00030000u

typedef struct {
    uint32_t saved, after;
    unsigned tested, mismatches;
    bool clock_off, restored;
} s31_tx_lab_result_t;
static bool s31_tx_lab_fault;

static void s31_tx_lab_fence(void) {
#ifdef __riscv
    __asm__ volatile("fence iorw,iorw" ::: "memory");
#endif
}

/* Caller holds the same core/interrupt exclusion as the existing RX capture.
 * No PHY calls, allocation, logging, scheduler calls or unbounded loops here. */
static s31_tx_lab_result_t s31_tx_lab_roundtrip(void) {
    static const uint32_t patterns[] = {0u, 511u | (512u << 10),
                                      512u | (511u << 10), 0x000fffffu};
    s31_tx_lab_result_t result = {0};
    result.saved = REG_READ(S31_TX_LAB_DC_REG);
    result.after = result.saved;
    result.clock_off = !(REG_READ(S31_TX_LAB_CLK_REG) & S31_TX_LAB_CLK_MASK);
    result.restored = true;
    if (!result.clock_off) return result;
    for (unsigned j = 0; j < sizeof(patterns)/sizeof(patterns[0]); ++j) {
        uint32_t value = (result.saved & ~S31_TX_LAB_FIELDS) | patterns[j];
        REG_WRITE(S31_TX_LAB_DC_REG, value);
        s31_tx_lab_fence();
        result.tested++;
        if (REG_READ(S31_TX_LAB_DC_REG) != value) result.mismatches++;
        result.clock_off = !(REG_READ(S31_TX_LAB_CLK_REG) & S31_TX_LAB_CLK_MASK);
        if (result.mismatches || !result.clock_off) break;
    }
    REG_WRITE(S31_TX_LAB_DC_REG, result.saved);
    s31_tx_lab_fence();
    result.after = REG_READ(S31_TX_LAB_DC_REG);
    result.restored = result.after == result.saved;
    return result;
}

static bool s31_tx_lab_command(const char *line) {
    if (!strcmp(line, "TXLAB?")) {
        reply("TXLAB 1 dc_roundtrip=1 native_burst=0 rf=disabled fault=%u\n",
              (unsigned)s31_tx_lab_fault);
        return true;
    }
    if (strcmp(line, "TXLABCHECK")) return false;
    if (s31_tx_lab_fault) {
        reply("ERR txlab_fault_reset_required\n");
        return true;
    }
    prepare_rx();
    phy_pbus_xpd_tx_off();
    phy_set_txclk_en(0);
    esp_ipc_isr_stall_other_cpu();
    taskENTER_CRITICAL(&dump_mux);
    s31_tx_lab_result_t result = s31_tx_lab_roundtrip();
    taskEXIT_CRITICAL(&dump_mux);
    esp_ipc_isr_release_other_cpu();
    prepare_rx();
    phy_set_txclk_en(0);
    phy_pbus_xpd_tx_off();
    result.clock_off = result.clock_off &&
        !(REG_READ(S31_TX_LAB_CLK_REG) & S31_TX_LAB_CLK_MASK);
    s31_tx_lab_fault = !result.restored || !result.clock_off;
    bool ok = result.tested == 4 && !result.mismatches && !s31_tx_lab_fault;
    reply("TXLABCHECK 1 ok=%u tested=%u mismatches=%u restored=%u clock_off=%u"
          " before=%08" PRIx32 " after=%08" PRIx32 " native_burst=0\n",
          (unsigned)ok, result.tested, result.mismatches,
          (unsigned)result.restored, (unsigned)result.clock_off,
          result.saved, result.after);
    return true;
}
#endif
