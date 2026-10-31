#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Telegram bot to play UNO in group chats
# Copyright (c) 2016 Jannes Höke <uno@jhoeke.de>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.


# Colors
RED = 'r'
BLUE = 'b'
GREEN = 'g'
YELLOW = 'y'
BLACK = 'x'

COLORS = (RED, BLUE, GREEN, YELLOW)

COLOR_ICONS = {
    RED: '❤️',
    BLUE: '💙',
    GREEN: '💚',
    YELLOW: '💛',
    BLACK: '⬛️'
}

# Values
ZERO = '0'
ONE = '1'
TWO = '2'
THREE = '3'
FOUR = '4'
FIVE = '5'
SIX = '6'
SEVEN = '7'
EIGHT = '8'
NINE = '9'
DRAW_TWO = 'draw'
REVERSE = 'reverse'
SKIP = 'skip'

VALUES = (ZERO, ONE, TWO, THREE, FOUR, FIVE, SIX, SEVEN, EIGHT, NINE, DRAW_TWO,
          REVERSE, SKIP)

# Colored cards of the Storm mode (storm.py)
SWAP = 'swap'
DROP = 'drop'
PLUS_ONE = 'plus1'

STORM_VALUES = (SWAP, DROP, PLUS_ONE)

# Special cards
CHOOSE = 'colorchooser'
DRAW_FOUR = 'draw_four'

SPECIALS = (CHOOSE, DRAW_FOUR)

# Wild cards that make the next player draw, and how many cards they add;
# the next player may challenge one as a bluff
DRAW_WILDS = {DRAW_FOUR: 4}

CARDS_CLASSIC = {
    "normal": {
        "b_0": "CAACAgQAAxUAAWrlFf8AAaDhpE9j_wxL_h16fY8GBAACXAIAAkY0dA12B_TTHzobwD0E",
        "b_1": "CAACAgQAAxUAAWrlFf9LumYvw1qzVp2WuALWA2GkAAJdAgACRjR0DSoVXNcesrlaPQQ",
        "b_2": "CAACAgQAAxUAAWrlFf9H3jNi4BVhrq2ZiHgfCJANAAJeAgACRjR0DeWdr7dcgGo-PQQ",
        "b_3": "CAACAgQAAxUAAWrlFf-nltlBm8_3dbHBnLIFlFyQAAJfAgACRjR0Ddz-Nc-du0S1PQQ",
        "b_4": "CAACAgQAAxUAAWrlFf-N4XE122B0Y2XlqfHxy7dZAAJgAgACRjR0DXE30hyqVmFaPQQ",
        "b_5": "CAACAgQAAxUAAWrlFf8Ky8pq5AtKOWmHwiGJ1TQJAAJhAgACRjR0DeHtJNTVEljKPQQ",
        "b_6": "CAACAgQAAxUAAWrlFf9NGp9-yPp55-m_pIWb84nQAAJiAgACRjR0DYnEGbINBny3PQQ",
        "b_7": "CAACAgQAAxUAAWrlFf-Ab5Po6-b3raXgerE7IOf0AAJjAgACRjR0DY0ZgBzJTiDmPQQ",
        "b_8": "CAACAgQAAxUAAWrlFf9v6Y7ZRKF1ubLBXouIoQFXAAJkAgACRjR0DfCIgoW9ciayPQQ",
        "b_9": "CAACAgQAAxUAAWrlFf9-A7RTr_eUeIVa-JJRILOIAAJlAgACRjR0DXieWAdOZuGfPQQ",
        "b_draw": "CAACAgQAAxUAAWrlFf-Xb4o9uY4trFHBcAABiGL0xQACZgIAAkY0dA348r-r0RJAuz0E",
        "b_skip": "CAACAgQAAxUAAWrlFf_nNHLf3xT2DgAB16O48o6PIwACaAIAAkY0dA3Q7pLudWmsdj0E",
        "b_reverse": "CAACAgQAAxUAAWrlFf_4tCMQVeEU-muAdtSt52uJAAJnAgACRjR0Dd3RDOfeaTHIPQQ",
        "g_0": "CAACAgQAAxUAAWrlFf-ddBS--HczPPd0IM5YCltFAAJrAgACRjR0DaAEXkV8Bb8XPQQ",
        "g_1": "CAACAgQAAxUAAWrlFf9BD3MD9C9IdFIvkgcPI2DoAAJsAgACRjR0DQT4jsh0_yAUPQQ",
        "g_2": "CAACAgQAAxUAAWrlFf9Kd4y9Rg3HXR1e6wVXyNcUAAJtAgACRjR0DZ3dFKaM5iyQPQQ",
        "g_3": "CAACAgQAAxUAAWrlFf_LgJgAASI5skTUYKv-3ElPmAACbgIAAkY0dA2eNqor983sij0E",
        "g_4": "CAACAgQAAxUAAWrlFf8Ee9QZP_PNIffjHsS7CZbhAAJvAgACRjR0DTUS4VzsFwlaPQQ",
        "g_5": "CAACAgQAAxUAAWrlFf9cF0oDjJNmNatlsAABTOJ_gQACcAIAAkY0dA16QCber0pxtz0E",
        "g_6": "CAACAgQAAxUAAWrlFf_e-dokMVuUGWNsiyETuzOBAAJxAgACRjR0DUK7ZSOwrq6ZPQQ",
        "g_7": "CAACAgQAAxUAAWrlFf-trMqarObmw38CGoDnRqWdAAJyAgACRjR0DcPI0jfW5-kGPQQ",
        "g_8": "CAACAgQAAxUAAWrlFf_ViRykqtKo9ZWHv31_KEBnAAJzAgACRjR0Dc3BmxB242euPQQ",
        "g_9": "CAACAgQAAxUAAWrlFf_QxLORW5U8qkegRoEhLwz9AAJ0AgACRjR0DQrN3nJFZyeKPQQ",
        "g_draw": "CAACAgQAAxUAAWrlFf8jZuj0YKZmhlx0cPjGIyFAAAJ1AgACRjR0DW2FVeKr-yATPQQ",
        "g_skip": "CAACAgQAAxUAAWrlFf8-ucCqzlEuPvDI6UsqyDFnAAJ3AgACRjR0DcF5dTrXEhfDPQQ",
        "g_reverse": "CAACAgQAAxUAAWrlFf89jxjNrM3sLJUhjchXpXrcAAJ2AgACRjR0Df00EDRm2BycPQQ",
        "r_0": "CAACAgQAAxUAAWrlFf_pBQR49KjqBplJDV6otvGBAAJ4AgACRjR0DR2__1McfQ0SPQQ",
        "r_1": "CAACAgQAAxUAAWrlFf-A2HqkL38yzOtRGkUKCgAB0gACeQIAAkY0dA1TFdYg5X70sz0E",
        "r_2": "CAACAgQAAxUAAWrlFf_1IAPCzRmy-8FieoU4xGXvAAJ6AgACRjR0DU5hThFQctRXPQQ",
        "r_3": "CAACAgQAAxUAAWrlFf8E7EH3gEXwdvra1FjnInJeAAJ7AgACRjR0DSsgVhA5sLs7PQQ",
        "r_4": "CAACAgQAAxUAAWrlFf8LeeVLBhrgbLgJ2mplIA8bAAJ8AgACRjR0Db52P_sL5qWWPQQ",
        "r_5": "CAACAgQAAxUAAWrlFf-7ej6asp6K5S067QHoQ81WAAJ9AgACRjR0DYfgM43r0bNBPQQ",
        "r_6": "CAACAgQAAxUAAWrlFf8sZH8ctZE0IlS3mHsb1HgwAAJ-AgACRjR0DdkllQycavdlPQQ",
        "r_7": "CAACAgQAAxUAAWrlFf8an_fALWcErFEZfBMS0DNTAAJ_AgACRjR0DTLwzF6nS_2QPQQ",
        "r_8": "CAACAgQAAxUAAWrlFf-oa27KsnIXBmN3GI4bWF1IAAKAAgACRjR0DVuEUY5CGl8RPQQ",
        "r_9": "CAACAgQAAxUAAWrlFf_DcZnYZyuuAg0rL6NWzzXgAAKBAgACRjR0DbIgAy3o7eFwPQQ",
        "r_draw": "CAACAgQAAxUAAWrlFf8FKXvhbxKa3rlsznTkcNk7AAKCAgACRjR0DWwthudISsJVPQQ",
        "r_skip": "CAACAgQAAxUAAWrlFf-3mfB6lHzobhz4-JPwowZgAAKEAgACRjR0Db-8L90ZSAoZPQQ",
        "r_reverse": "CAACAgQAAxUAAWrlFf-PkJZivNFLtvSrEuSSyY7JAAKDAgACRjR0DXVxLnDqxRgtPQQ",
        "y_0": "CAACAgQAAxUAAWrlFf-tYJ6txjEGdHJhLK4STvIOAAKFAgACRjR0DVSHDgYobnOPPQQ",
        "y_1": "CAACAgQAAxUAAWrlFf-9HRZFirMGPJ9yxzZwHvn_AAKGAgACRjR0DVWjUFjNAAHwKT0E",
        "y_2": "CAACAgQAAxUAAWrlFf_OoZbCSlQpN1j9R51IzIe_AAKHAgACRjR0DcyYdhTDga-_PQQ",
        "y_3": "CAACAgQAAxUAAWrlFf-ak0EgL2UYV4or4yC7N2frAAKIAgACRjR0DbKwQhlz0DIZPQQ",
        "y_4": "CAACAgQAAxUAAWrlFf_2CzUCXq-sLTlvQiG--8KYAAKJAgACRjR0DZ8GQfeN2YBWPQQ",
        "y_5": "CAACAgQAAxUAAWrlFf-rm4tbBH_HTMt1k_hvcRp7AAKKAgACRjR0DQI0PzwcoDeMPQQ",
        "y_6": "CAACAgQAAxUAAWrlFf9BLj_fZngImWLMDT7AAdkUAAKLAgACRjR0DQlhW3RdmGrDPQQ",
        "y_7": "CAACAgQAAxUAAWrlFf9L6n4qqEqHUGsYlVrun5M0AAKMAgACRjR0Dfct3r7OCdSwPQQ",
        "y_8": "CAACAgQAAxUAAWrlFf_nN0rqwAABtNbaTpp3rMguzgACjQIAAkY0dA26whisV_oveD0E",
        "y_9": "CAACAgQAAxUAAWrlFf_nmq_KiD_ZCq94UoeGDEEZAAKOAgACRjR0DUlobxN4k4WIPQQ",
        "y_draw": "CAACAgQAAxUAAWrlFf9QsvIpUu2IjbcSDdHGC3_9AAKPAgACRjR0DV4e9QABh166bz0E",
        "y_skip": "CAACAgQAAxUAAWrlFf8IXzR6cD7YbrCBqP019WzKAAKRAgACRjR0Df9qcVXY3mLqPQQ",
        "y_reverse": "CAACAgQAAxUAAWrlFf-W3osMS3FckdpTnyft7qWwAAKQAgACRjR0DbsH4jGJ773rPQQ",
        "draw_four": "CAACAgQAAxUAAWrlFf9OxMy2bzh3UHMpYixqfi05AAJqAgACRjR0Dd5CuwoxyGVePQQ",
        "colorchooser": "CAACAgQAAxUAAWrlFf-QamzzRQHQZX44Hw7eX4_6AAJpAgACRjR0Dc5XKL-wIwXUPQQ",
    },
    "not_playable": {
        "b_0": "CAACAgQAAxUAAWrlFf_N-Vj-D-2FAqG_zjo_rUW8AAKSAgACRjR0DSd7vDCNDwzSPQQ",
        "b_1": "CAACAgQAAxUAAWrlFf_q1P1i7ZRa8U2V1yNtck-vAAKTAgACRjR0DUD9wYZCxxV3PQQ",
        "b_2": "CAACAgQAAxUAAWrlFf8Zq5JFplMT1A_B1epvuQj-AAKUAgACRjR0DZjfB7P3mLFrPQQ",
        "b_3": "CAACAgQAAxUAAWrlFf-YMK6sct4cLQKB5WcEop2CAAKVAgACRjR0DUW76tSxB2ovPQQ",
        "b_4": "CAACAgQAAxUAAWrlFf8TaWb2PLl0q2lr9kfv95vEAAKWAgACRjR0DW-GmXRrCO-IPQQ",
        "b_5": "CAACAgQAAxUAAWrlFf-Kim-VQBT3UiP1P0AfFDqEAAKXAgACRjR0DQySoTUIjYyKPQQ",
        "b_6": "CAACAgQAAxUAAWrlFf9csPgKtlimL89nNpLo1CSQAAKYAgACRjR0DZFRw69TtZB1PQQ",
        "b_7": "CAACAgQAAxUAAWrlFf-yGt7x8UhizLIFtZylfCB8AAKZAgACRjR0DRhY4tb7uWxMPQQ",
        "b_8": "CAACAgQAAxUAAWrlFf9NZeLZ9pGfIyRbW5Z7E-hEAAKaAgACRjR0DZqRQE_G4fqLPQQ",
        "b_9": "CAACAgQAAxUAAWrlFf-mDZor5zqSkkxaHRZkISbgAAKbAgACRjR0DY59B-YSksFbPQQ",
        "b_draw": "CAACAgQAAxUAAWrlFf_JDo5dx-RvsawrCUnaUgFnAAKcAgACRjR0DUnmgtJinKS_PQQ",
        "b_skip": "CAACAgQAAxUAAWrlFf-L1G8l74hYDxaC39vvXRCiAAKeAgACRjR0DeakT38b971mPQQ",
        "b_reverse": "CAACAgQAAxUAAWrlFf82qXJo9WjAWQNLOxkLac2lAAKdAgACRjR0DWhlJwuDsQABfD0E",
        "g_0": "CAACAgQAAxUAAWrlFf-TYAedGHrPxgHUu2Ra3-MUAAKhAgACRjR0DQHPDEQpvXR-PQQ",
        "g_1": "CAACAgQAAxUAAWrlFf_mxuSABLhYB-iRvaA1p-syAAKiAgACRjR0DSaRowAB2T1XtD0E",
        "g_2": "CAACAgQAAxUAAWrlFf8p3rnh8oeu4_rIE8pIxRSYAAKjAgACRjR0DWhnywwiiiKQPQQ",
        "g_3": "CAACAgQAAxUAAWrlFf95h4XQ0j3uCec5eufiRkhVAAKkAgACRjR0DQtwvZd_KD9UPQQ",
        "g_4": "CAACAgQAAxUAAWrlFf8q0uvRmotrEFzMUSW51S-OAAKlAgACRjR0DWY9LBRw5il2PQQ",
        "g_5": "CAACAgQAAxUAAWrlFf-Id7YpODT3SNpkqGpqN4T4AAKmAgACRjR0DcP35va88E63PQQ",
        "g_6": "CAACAgQAAxUAAWrlFf93unpiB_FwCpwDcv4EJx-gAAKnAgACRjR0DWQetEPX25xHPQQ",
        "g_7": "CAACAgQAAxUAAWrlFf9LKfVuZX9tKfPU8vtXz6fVAAKoAgACRjR0DXnbUX-Bt3ZTPQQ",
        "g_8": "CAACAgQAAxUAAWrlFf8DD6kivDSIZvEhHTaMVfr1AAKpAgACRjR0DR3IlLOP9RWnPQQ",
        "g_9": "CAACAgQAAxUAAWrlFf8mDTYlzvMGCWu0JcAAAZ5TWgACqgIAAkY0dA1F4zteZbf-gj0E",
        "g_draw": "CAACAgQAAxUAAWrlFf_1DwhrnTGSZf9-z9osf_jYAAKrAgACRjR0DT_LVCTHzs_FPQQ",
        "g_skip": "CAACAgQAAxUAAWrlFf8lKfg4JBskTRoLRwABkKlwqAACrQIAAkY0dA2T3QqAlzwSKT0E",
        "g_reverse": "CAACAgQAAxUAAWrlFf9m1hTOJjItxDX6YRsfTkkmAAKsAgACRjR0DQRUTTcfK0-YPQQ",
        "r_0": "CAACAgQAAxUAAWrlFf81WPHbUtjStsu9G87Y1XdiAAKuAgACRjR0DX8J_fzPsAK2PQQ",
        "r_1": "CAACAgQAAxUAAWrlFf-TVudhJnqb6jfgGRdnt4huAAKvAgACRjR0DZj2r9eAIsHtPQQ",
        "r_2": "CAACAgQAAxUAAWrlFf9dZHKWSJsHudn2k10REoolAAKwAgACRjR0DRTKyCVJb8j2PQQ",
        "r_3": "CAACAgQAAxUAAWrlFf8927AIMD8IfL-Am0Zsn_r9AAKxAgACRjR0DXeCM84c7wWwPQQ",
        "r_4": "CAACAgQAAxUAAWrlFf8AAet5zaCwzyI5CToIKJ6gCAACsgIAAkY0dA3-sc5hq3F3Pj0E",
        "r_5": "CAACAgQAAxUAAWrlFf_RJwRIW6xKQm-Y4OifMVVgAAKzAgACRjR0DexqzUEgp-JiPQQ",
        "r_6": "CAACAgQAAxUAAWrlFf8JHMlFMQakn9btF4ZOMoKgAAK0AgACRjR0DWGwyWWHm7tOPQQ",
        "r_7": "CAACAgQAAxUAAWrlFf-3sA6eP-9rkGdn1FJTbwU5AAK1AgACRjR0DQ7fBoCZNMfWPQQ",
        "r_8": "CAACAgQAAxUAAWrlFf9Q4DFuzZ6IqoWZ24_AMn2KAAK2AgACRjR0Db_h4qRyVPeoPQQ",
        "r_9": "CAACAgQAAxUAAWrlFf9g9EOC23NPfZdVKTjBjdvXAAK3AgACRjR0DX-lpn7tQ-K5PQQ",
        "r_draw": "CAACAgQAAxUAAWrlFf_7AZYKVKpJWZj6tSo6JNTXAAK4AgACRjR0DSy6wPLd8S90PQQ",
        "r_skip": "CAACAgQAAxUAAWrlFf-b08nRPTbqGyuZAqhxD_EoAAK6AgACRjR0DYNiKrUcPSqPPQQ",
        "r_reverse": "CAACAgQAAxUAAWrlFf_WjHRVolUzuyrIyWPiETRsAAK5AgACRjR0DaEtcdgmeyDaPQQ",
        "y_0": "CAACAgQAAxUAAWrlFf8ppGgmXiNdgOSo9VkhSo94AAK7AgACRjR0DXFmoPtTeriNPQQ",
        "y_1": "CAACAgQAAxUAAWrlFf86YycNY6gy8nQubQtYS08OAAK8AgACRjR0DWH2SVJcYe5yPQQ",
        "y_2": "CAACAgQAAxUAAWrlFf99Ez__ks2tmchpb4yxusWwAAK9AgACRjR0Da5YvJJ_cpM3PQQ",
        "y_3": "CAACAgQAAxUAAWrlFf-KhzoTUEKThQT7s-0tBEFFAAK-AgACRjR0DUE23MtX1ztUPQQ",
        "y_4": "CAACAgQAAxUAAWrlFf8kE0OTtGKXSJkerrZT4qrkAAK_AgACRjR0DQwX-vVaZ8T6PQQ",
        "y_5": "CAACAgQAAxUAAWrlFf86ri-4yKG9jNmHjxyC_hWqAALAAgACRjR0DXKaR3iGkytmPQQ",
        "y_6": "CAACAgQAAxUAAWrlFf8PxVtU563UotEddJ7a3WoNAALBAgACRjR0DaieFR6h4zfaPQQ",
        "y_7": "CAACAgQAAxUAAWrlFf_A0Gj7NEgAAT-l0WYIat53EgACwgIAAkY0dA0j1PeLl2gy5j0E",
        "y_8": "CAACAgQAAxUAAWrlFf9-iSuJ-io69kIi8coYCMnMAALDAgACRjR0DZ00Pvvc_Sv6PQQ",
        "y_9": "CAACAgQAAxUAAWrlFf8ie9fTsX0o0xMq-kTBB7QtAALEAgACRjR0DeScBHamBZ18PQQ",
        "y_draw": "CAACAgQAAxUAAWrlFf-EYGg_dGdy_h9ZbRCPjXZlAALFAgACRjR0DWphROOmDxx7PQQ",
        "y_skip": "CAACAgQAAxUAAWrlFf-Et1Z1_TPNtn-l0rGYMnY4AALHAgACRjR0DSnt-E2evrLHPQQ",
        "y_reverse": "CAACAgQAAxUAAWrlFf8tQaI__L5PH53gyZEZQ-qRAALGAgACRjR0DajvUuwjt9p1PQQ",
        "draw_four": "CAACAgQAAxUAAWrlFf_NaHh8tmnoV47bjgRY9vPEAAKgAgACRjR0DXxsbzd0MissPQQ",
        "colorchooser": "CAACAgQAAxUAAWrlFf_bUWA5GXAwMhC2TImpfoYTAAKfAgACRjR0DZnYe0MMgf6aPQQ",
    },
}

CARDS_CLASSIC_COLORBLIND = {
    "normal": {
        "colorchooser": "CAADBAADrg4AAvX2mVEpx_BiDIE5nQI",
        "draw_four": "CAADBAADYRAAArnkmVGmqXHhjWEBxAI",
        "r_0": "CAADBAAD6A8AAn_ckVHPWHqiBR_3jAI",
        "r_1": "CAADBAAD5Q0AAg-ImVEx-blQI88RrQI",
        "r_2": "CAADBAAD1g0AAuMjmVEkQsVhN49DMAI",
        "r_3": "CAADBAADlhAAAqy4mVHWovoaWfQG_gI",
        "r_4": "CAADBAADCRoAAqf_kVFnl8ACL1rjpwI",
        "r_5": "CAADBAADVw8AAjmamVEEv2TVeL9cpQI",
        "r_6": "CAADBAADHQ4AAuuUkVH2I-yn6nRBVAI",
        "r_7": "CAADBAADNQ8AArP1kVF5rqHtk0pQ-AI",
        "r_8": "CAADBAAD1BAAAuQDkVEPiIodUi6WvwI",
        "r_9": "CAADBAAD2Q4AAq1nkFHM6z5C0Kff2QI",
        "r_draw": "CAADBAADvQ8AAqZukFGEmkRSoSZQEwI",
        "r_reverse": "CAADBAAD5RAAAg89mVE8-EY_2DifcAI",
        "r_skip": "CAADBAADRg4AAp8bmVFOC6xdEZZRwwI",
        "g_0": "CAADBAADTg4AAoQxmFF07jR_vfB4xgI",
        "g_1": "CAADBAADQg4AAhkgmFGlsif9nNtXwgI",
        "g_2": "CAADBAAD2BUAAue_mFGENiPSjZxbiQI",
        "g_3": "CAADBAADpw4AAjO9mFHAOz8KD2n7BwI",
        "g_4": "CAADBAADRhAAAqF7kFEcwLalLfDfaAI",
        "g_5": "CAADBAADAg8AAqXLmFHJyg2F_ybbvwI",
        "g_6": "CAADBAADVhYAAtK7mVGigRq_EkCuVgI",
        "g_7": "CAADBAAD2RIAArccmFEj-8LIVNAbsgI",
        "g_8": "CAADBAAD6AwAAuvmmFHBRarMimOWawI",
        "g_9": "CAADBAADExEAAsNkmVFr8DaHGOwsggI",
        "g_draw": "CAADBAADhA8AArxYmVH9ch5Jp00AAboC",
        "g_reverse": "CAADBAADMhAAAvVOmFGH284LIY7cegI",
        "g_skip": "CAADBAADbBcAAqinkVEOwkJtDRfk2gI",
        "b_0": "CAADBAAD-BAAAkj8kFG61GJdw29QOAI",
        "b_1": "CAADBAADcRMAAu-EmFFT1i4LcqO4OQI",
        "b_2": "CAADBAAD0xQAAqVhmVHyrFSAbxtfjwI",
        "b_3": "CAADBAADNg0AAn-xmFHev8IdF_ie0wI",
        "b_4": "CAADBAADlQ4AAjZamVFcIL_pVB5cFwI",
        "b_5": "CAADBAADrgwAAuL5mVHvEBZ8CG5p5QI",
        "b_6": "CAADBAADDhUAAuGRmVGQYvmEOxczBAI",
        "b_7": "CAADBAADIxEAAv_dmFEuVt39kkgZgwI",
        "b_8": "CAADBAAD2w0AAoE6kVHG7WscV4F2hwI",
        "b_9": "CAADBAADvQ0AArRMmVErWaSRP_giKQI",
        "b_draw": "CAADBAADlw4AAjF_kFHPWSoYKBwtwQI",
        "b_reverse": "CAADBAADog8AAqDJmVEJQp5WocnUnQI",
        "b_skip": "CAADBAAD-QwAAgbZmFGltUlnslDNUQI",
        "y_0": "CAADBAADrQ4AAr5WmVHNf69eBn2YOAI",
        "y_1": "CAADBAADcg8AAmqKmVHfVeUI3u_i7AI",
        "y_2": "CAADBAADkA4AAuDImFEQ8qjFlcKplQI",
        "y_3": "CAADBAAD-QwAAmromFGAqVn-Y8N72wI",
        "y_4": "CAADBAADjQ4AAmNLmFG80k7kfgx1NAI",
        "y_5": "CAADBAADqQ8AAmgYmFH1_ey_bMQNYwI",
        "y_6": "CAADBAADdQ0AAuWcmFEbG_gm1wGYCQI",
        "y_7": "CAADBAAD6QwAApQAAZhRI8OfRvLX3vkC",
        "y_8": "CAADBAADARAAAi-2kVEifJ-O9WVilgI",
        "y_9": "CAADBAADxA0AAhQ8mFHjnl9tUCHSLAI",
        "y_draw": "CAADBAADzw4AAncZmVEhLhX17eqX8AI",
        "y_reverse": "CAADBAADTxAAAqgFmVEJRBw4eWgnDwI",
        "y_skip": "CAADBAADPhYAAiGbkFG9hptFPLgj7wI",
    },
    "not_playable": {
        "colorchooser": "CAADBAADpQ4AAlfDmFFHGkwyGFeCFQI",
        "draw_four": "CAADBAADMRMAAv7amFHvKGLoNyFbNQI",
        "r_0": "CAADBAADsBMAAuGdkFHTZ-jl4eNn-gI",
        "r_1": "CAADBAADVA4AAhpfkFEKt19qveGSPgI",
        "r_2": "CAADBAADrw0AAoWsmVHguULNoYJwUwI",
        "r_3": "CAADBAADzxMAAjvkkFFdtKJu5WGwUgI",
        "r_4": "CAADBAAD1Q8AAoHZkFFvyQnFHzfwiQI",
        "r_5": "CAADBAADWxEAAvkHkFGUo86qxKV0kwI",
        "r_6": "CAADBAAD_hIAAjx0mVGmlm-b_FHQBAI",
        "r_7": "CAADBAADmhEAAslomFHOv7bqcDJkDAI",
        "r_8": "CAADBAADtw0AAgqVmVG2HdSbcJYxZgI",
        "r_9": "CAADBAADNxEAAuF6mVE3WzTMJkSVAgI",
        "r_draw": "CAADBAADVxAAAiNukFE1K2xORNnfMwI",
        "r_reverse": "CAADBAADQxMAAvH0mVHKznpt-uu9ngI",
        "r_skip": "CAADBAADZA4AApbPkFFB9E2Px-HFpgI",
        "g_0": "CAADBAAD8w4AAjDEmFG7DwKggUEj9QI",
        "g_1": "CAADBAAD2g0AAo_DmVHIPG84WdIo1wI",
        "g_2": "CAADBAADEhEAAoRXmVGIG2nuN45P6AI",
        "g_3": "CAADBAADug8AAsSRmFFzk0TcRuG8VAI",
        "g_4": "CAADBAADrQ8AAvgmkFESfo9BjF7-3gI",
        "g_5": "CAADBAADVhAAAnPqkFFtxtFX9HlT-AI",
        "g_6": "CAADBAADMg8AAiSBmFHIQw1jFjv6UwI",
        "g_7": "CAADBAADvREAAv0BkVGDq3H1DCq_DQI",
        "g_8": "CAADBAADWQ4AAhOEkVG96JDgCtFrEwI",
        "g_9": "CAADBAAD2xYAAruDmFFAUMFryEwjoAI",
        "g_draw": "CAADBAADLA4AAu9tkVGTzBbeeYydIQI",
        "g_reverse": "CAADBAADVAwAAhYYmFExJS0ozE8-rAI",
        "g_skip": "CAADBAADYg4AAulsmFHxOkaz9OsTiwI",
        "b_0": "CAADBAADVxUAAtnOkFEIAAGw5CZEIxgC",
        "b_1": "CAADBAAD1RAAAnQqkFF9kDqD0wp3ngI",
        "b_2": "CAADBAADZg4AAvcUmVHTXwldirf1hAI",
        "b_3": "CAADBAADfBAAAkX1mVHw0CWX0h31iQI",
        "b_4": "CAADBAADPBAAAuTCmFFDpvXzes4qjwI",
        "b_5": "CAADBAADTQ4AAsWQmVHcrxDQUWOB4AI",
        "b_6": "CAADBAAD_hAAAoUhmVG8kjd65J8EngI",
        "b_7": "CAADBAADlRAAArtjkFGko5TuFNnncwI",
        "b_8": "CAADBAADZQ8AAltEmFE_fDYIXBrV3QI",
        "b_9": "CAADBAADrhAAAtM-mVGwhrWTD9IaYgI",
        "b_draw": "CAADBAADtQ0AAnVbmFGC1hI60JaOQQI",
        "b_reverse": "CAADBAADShEAAlcOmFHStPeFzfVIEwI",
        "b_skip": "CAADBAAD_xEAAgZFmVFMRA1J8Y1gxAI",
        "y_0": "CAADBAAD7xAAAqjjmFHnCu7eKJvSBgI",
        "y_1": "CAADBAADJQwAAp6tmFE2zDPVMieQ2QI",
        "y_2": "CAADBAADNA4AAl2mmVFpQOxJ41gk_gI",
        "y_3": "CAADBAAD3A4AAsxPmFGyZFv42UlxAQI",
        "y_4": "CAADBAADwg8AAm88kVEc9HZpl2gmzQI",
        "y_5": "CAADBAAD5hIAAkQ6mFHS-aGVuYZAnAI",
        "y_6": "CAADBAADvQ8AAs3RmVHVkVBfEF7eIwI",
        "y_7": "CAADBAAD1gwAAjlbmFGGH6rBdqP8QQI",
        "y_8": "CAADBAADbg8AAqvXkVH1ESeZFcGVrgI",
        "y_9": "CAADBAADOQ8AAnjokVG96pmCP7aZ3AI",
        "y_draw": "CAADBAAD6w4AAgsJmVETUteFwqTVJgI",
        "y_reverse": "CAADBAADtg8AAqiFmFFwothyN9TrXwI",
        "y_skip": "CAADBAADSxEAAhcSmFGu_F5LffmsZgI",
    },
}

# The Storm cards (UNOBotGamesArenaV pack, positions 63-80 and their grey versions 83-100),
# the same in every card set
STICKERS_STORM = {
    "b_swap": "CAACAgQAAxUAAWrlyoKWS1Bjg7yL23OMI--RDaMYAAL2EAACmT3BU2VPSY1MTrvJPQQ",
    "b_drop": "CAACAgQAAxUAAWrlyoLiq743ZxfMRAVTSTFxJsKPAAIaEgACD9apU7YlVqm7OWVrPQQ",
    "b_plus1": "CAACAgQAAxUAAWrlyoKaP7u5MLryTA9Z3u7BkytDAAISAwACRjR0DbXufZIxCfEJPQQ",
    "g_swap": "CAACAgQAAxUAAWrlyoIOXhseDXm7zHy0xMSw6KjsAAKGEAACeWjBU0dntrBFC-poPQQ",
    "g_drop": "CAACAgQAAxUAAWrlyoI6hz1bnqnlmLBGDRw8B_F5AALpEgAC9OOgUyuq_z5X2FchPQQ",
    "g_plus1": "CAACAgQAAxUAAWrlyoJr_Vik7kxYDdX8ex54-zlsAAIUAwACRjR0DdFSlwRZPvgPPQQ",
    "r_swap": "CAACAgQAAxUAAWrlyoIuHeC9mMInQX8xtvnAhqqUAAKLGQAC5ce5U1oEJ2DXMrHsPQQ",
    "r_drop": "CAACAgQAAxUAAWrlyoKpD1pqwD3SRInCMq99CjvdAAIZGQAC9NCgU8BSJZIiZe1LPQQ",
    "r_plus1": "CAACAgQAAxUAAWrlyoIRk17Jetf8DGQ_OoHN9SxwAAIWAwACRjR0DQYYbdQqcyB-PQQ",
    "y_swap": "CAACAgQAAxUAAWrlyoKkJcHnBnb6ARA5PMh1J1gwAAIdEgACKnHBU4wRW_T_nGDmPQQ",
    "y_drop": "CAACAgQAAxUAAWrlyoLhjW8hVdKirX7RgylgNG0FAAIMFgACL-OoU6oF6icl9tClPQQ",
    "y_plus1": "CAACAgQAAxUAAWrlyoK8LNmomKa8N0PC2blHqNbeAAIYAwACRjR0Dadt_AuXiMIdPQQ",
}

STICKERS_STORM_GREY = {
    "b_swap": "CAACAgQAAxUAAWrlyoKz5I8IDPFWfoOz8Kc9a97yAAJuEwACT2zBUwHKogu3o8ZqPQQ",
    "b_drop": "CAACAgQAAxUAAWrlyoI4-rbmsJlKwZhBnX7D-esIAAIOFAACz1eoU3vpJmvbx_n0PQQ",
    "b_plus1": "CAACAgQAAxUAAWrlyoKKirTTGSIxQt9s5vI1SaulAAITAwACRjR0Dd_KuaRWO8qQPQQ",
    "g_swap": "CAACAgQAAxUAAWrlyoIB5DftuLz4aGANwMqz3yTDAAL7DQACVQ7AU89I2SQj3QeNPQQ",
    "g_drop": "CAACAgQAAxUAAWrlyoJCsPLhH42UGZhEzEoTCv0iAAI5EgACD62gUwm16witpyYXPQQ",
    "g_plus1": "CAACAgQAAxUAAWrlyoJYRVscktYuZdBNoXZKHHH4AAIVAwACRjR0DbfdlUnwGecJPQQ",
    "r_swap": "CAACAgQAAxUAAWrlyoKLsadFFZhCN-2A2nf-qb5wAAKUEQACZrbAUyCUj5ZT-yPbPQQ",
    "r_drop": "CAACAgQAAxUAAWrlyoLAAzyz5HVJHSd1yjsGR07KAAKXFAACfo-gU8vFYersPCezPQQ",
    "r_plus1": "CAACAgQAAxUAAWrlyoIX4AABzGni_QeB7EqQwflWBwACFwMAAkY0dA1FGO-G_Pq9vD0E",
    "y_swap": "CAACAgQAAxUAAWrlyoIObsaysE-30o4YmsB_YeTqAAKDEAACEKnBU_VUEqxc-yVMPQQ",
    "y_drop": "CAACAgQAAxUAAWrlyoIwduwGP_1czG6qYw0wtYzbAALXEwACfjSoU5rufV2Mq9FyPQQ",
    "y_plus1": "CAACAgQAAxUAAWrlyoJao_Or3YCJi34j03KqS6cIAAIZAwACRjR0DVg9rMreSVSIPQQ",
}

STICKERS_OPTIONS = {
    "option_draw": "CAACAgQAAxUAAWrlFf9h0bYOTGPFpUp1A4Kv4CBNAAICBAACRjR0DY2i3mgWBOLPPQQ",
    "option_pass": "CAACAgQAAxUAAWrlFf-OlZ-6xJyPMwgZq_P2gSrNAAIDBAACRjR0DccpKJfeExUdPQQ",
    "option_bluff": "CAACAgQAAxUAAWrlFf_Gmte7cCdxL642RDa_LdDtAAIIBAACRjR0DX75Tald1LzePQQ",
    "option_info": "CAACAgQAAxUAAWrlFf-LnHziyR6Xd2OxtNSp0TgyAAIKBAACRjR0DU18JXPNcuo3PQQ",
}

from config import DEFAULT_CARDSET

CARDSETS = {
    "classic": CARDS_CLASSIC,
    "colorblind": CARDS_CLASSIC_COLORBLIND,
}
_selected_cardset = CARDSETS.get(DEFAULT_CARDSET, CARDS_CLASSIC)

STICKERS = {
    **_selected_cardset["normal"],
    **STICKERS_STORM,
    **STICKERS_OPTIONS,
}

STICKERS_GREY = {
    **_selected_cardset["not_playable"],
    **STICKERS_STORM_GREY,
}


class Card(object):
    """This class represents an UNO card"""

    def __init__(self, color, value, special=None):
        self.color = color
        self.value = value
        self.special = special

    def __str__(self):
        if self.special:
            return self.special
        else:
            return '%s_%s' % (self.color, self.value)

    def __repr__(self):
        if self.special:
            return '%s%s%s' % (COLOR_ICONS.get(self.color, ''),
                               COLOR_ICONS[BLACK],
                               ' '.join([s.capitalize()
                                         for s in self.special.split('_')]))
        else:
            value = '+1' if self.value == PLUS_ONE else self.value.capitalize()
            return '%s%s' % (COLOR_ICONS[self.color], value)

    def __eq__(self, other):
        """Needed for sorting the cards"""
        return str(self) == str(other)

    def __lt__(self, other):
        """Needed for sorting the cards"""
        return str(self) < str(other)


def label(card):
    """Readable card name for plain-text messages, e.g. '💚+2' or '⬛️+4'"""
    return (repr(card).replace('Draw Four', '+4').replace('Draw', '+2')
            .replace('Colorchooser', 'Color Chooser'))


def is_draw_wild(card):
    """Whether `card` is a wild that makes the next player draw (a +4)"""
    return card is not None and card.special in DRAW_WILDS


def from_str(string):
    """Decodes a Card object from a string, preserving color for special cards if present"""
    if not string:
        return None
    if ':' in string:
        special_or_color, extra = string.split(':', 1)
        if special_or_color in SPECIALS:
            return Card(extra, None, special=special_or_color)
        if extra in SPECIALS:
            return Card(special_or_color, None, special=extra)
        return Card(special_or_color, extra)
    elif string in SPECIALS:
        return Card(None, None, string)
    elif '_' in string:
        color, value = string.split('_', 1)
        return Card(color, value)
    else:
        return Card(None, None, string)
