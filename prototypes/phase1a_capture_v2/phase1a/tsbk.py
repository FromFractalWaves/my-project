# Standalone P25 TSBK (Trunking Signalling Block) parser
#
# Derived from OP25 trunking.py and helper_funcs.py
# Original Copyright 2011-2017 Max H. Parke KA1RBI
# Original Copyright 2017-2021 Graham Norbury
#
# This file is part of OP25
#
# OP25 is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
#
# OP25 is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
# License for more details.
#
# You should have received a copy of the GNU General Public License
# along with OP25; see the file COPYING. If not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Boston, MA
# 02110-1301, USA.
#
# Modifications for albatross1a capture pipeline:
#   - Extracted minimal TSBK decoding (grant, grant_update, iden_up)
#   - Removed full trunking state machine, voice tracking, patches
#   - Returns structured dicts instead of mutating internal state
#

import ctypes


def get_ordinals(s):
    """Convert a byte sequence to an integer (big-endian)."""
    t = 0
    if type(s) is int:
        return s
    elif type(s) is not str and isinstance(s, bytes):
        for c in s:
            t = (t << 8) + c
    else:
        for c in s:
            t = (t << 8) + ord(c)
    return t


class TSBKParser:
    """Minimal P25 TSBK parser for channel grant extraction.

    Maintains a frequency identifier table (populated from iden_up opcodes)
    and decodes group voice channel grants into structured dicts.

    Usage::

        parser = TSBKParser()
        while True:
            msg = queue.delete_head()
            result = parser.process_qmsg(msg)
            if result is not None:
                print(result)
    """

    def __init__(self):
        self.freq_table = {}

    def channel_id_to_frequency(self, id):
        """Resolve a channel ID to a frequency in Hz using the freq table."""
        table = (id >> 12) & 0xf
        channel = id & 0xfff
        if table not in self.freq_table:
            return None
        if 'tdma' not in self.freq_table[table]:
            return self.freq_table[table]['frequency'] + self.freq_table[table]['step'] * channel
        return self.freq_table[table]['frequency'] + self.freq_table[table]['step'] * int(channel / self.freq_table[table]['tdma'])

    def get_tdma_slot(self, id):
        """Return the TDMA slot number for a channel ID, or None if FDMA."""
        table = (id >> 12) & 0xf
        channel = id & 0xfff
        if table not in self.freq_table:
            return None
        if 'tdma' not in self.freq_table[table]:
            return None
        if self.freq_table[table]['tdma'] < 2:
            return None
        return channel & 1

    def decode_tsbk(self, tsbk, nac):
        """Decode a single TSBK and return a structured dict, or None.

        Args:
            tsbk: Integer representation of the 10-byte TSBK body
                  (already converted via get_ordinals).
            nac: The 12-bit NAC value from the message header.

        Returns:
            A dict describing the decoded TSBK, or None if the opcode
            is not one we handle.
        """
        tsbk = tsbk << 16  # account for missing CRC (matches OP25 convention)
        opcode = (tsbk >> 88) & 0x3f

        if opcode == 0x00:  # grp_v_ch_grant
            mfrid = (tsbk >> 80) & 0xff
            if mfrid == 0x90:
                # Motorola group regroup -- skip
                return None
            opts = (tsbk >> 72) & 0xff
            ch = (tsbk >> 56) & 0xffff
            ga = (tsbk >> 40) & 0xffff
            sa = (tsbk >> 16) & 0xffffff
            f = self.channel_id_to_frequency(ch)
            return {
                "type": "grant",
                "opcode": opcode,
                "nac": nac,
                "tgid": ga,
                "frequency": f,
                "srcaddr": sa,
                "channel_id": ch,
                "tdma_slot": self.get_tdma_slot(ch),
            }

        elif opcode == 0x02:  # grp_v_ch_grant_updt
            mfrid = (tsbk >> 80) & 0xff
            if mfrid == 0x90:
                # Motorola variant: single channel + supergroup + srcaddr
                ch = (tsbk >> 56) & 0xffff
                sg = (tsbk >> 40) & 0xffff
                sa = (tsbk >> 16) & 0xffffff
                f = self.channel_id_to_frequency(ch)
                return {
                    "type": "grant_update",
                    "opcode": opcode,
                    "nac": nac,
                    "tgid": sg,
                    "frequency": f,
                    "srcaddr": sa,
                    "channel_id": ch,
                    "tdma_slot": self.get_tdma_slot(ch),
                }
            else:
                ch1 = (tsbk >> 64) & 0xffff
                ga1 = (tsbk >> 48) & 0xffff
                ch2 = (tsbk >> 32) & 0xffff
                ga2 = (tsbk >> 16) & 0xffff
                f1 = self.channel_id_to_frequency(ch1)
                f2 = self.channel_id_to_frequency(ch2)
                return {
                    "type": "grant_update",
                    "opcode": opcode,
                    "nac": nac,
                    "tgid": ga1,
                    "frequency": f1,
                    "srcaddr": None,
                    "channel_id": ch1,
                    "tdma_slot": self.get_tdma_slot(ch1),
                    "tgid2": ga2,
                    "frequency2": f2,
                    "channel_id2": ch2,
                    "tdma_slot2": self.get_tdma_slot(ch2),
                }

        elif opcode == 0x03:  # grp_v_ch_grant_updt_exp
            mfrid = (tsbk >> 80) & 0xff
            if mfrid == 0x90:
                # Motorola group regroup grant update
                ch1 = (tsbk >> 64) & 0xffff
                sg1 = (tsbk >> 48) & 0xffff
                ch2 = (tsbk >> 32) & 0xffff
                sg2 = (tsbk >> 16) & 0xffff
                f1 = self.channel_id_to_frequency(ch1)
                f2 = self.channel_id_to_frequency(ch2)
                return {
                    "type": "grant_update",
                    "opcode": opcode,
                    "nac": nac,
                    "tgid": sg1,
                    "frequency": f1,
                    "srcaddr": None,
                    "channel_id": ch1,
                    "tdma_slot": self.get_tdma_slot(ch1),
                    "tgid2": sg2,
                    "frequency2": f2,
                    "channel_id2": ch2,
                    "tdma_slot2": self.get_tdma_slot(ch2),
                }
            elif mfrid == 0:
                opts = (tsbk >> 72) & 0xff
                ch1 = (tsbk >> 48) & 0xffff
                ch2 = (tsbk >> 32) & 0xffff
                ga = (tsbk >> 16) & 0xffff
                f = self.channel_id_to_frequency(ch1)
                return {
                    "type": "grant_update",
                    "opcode": opcode,
                    "nac": nac,
                    "tgid": ga,
                    "frequency": f,
                    "srcaddr": None,
                    "channel_id": ch1,
                    "tdma_slot": self.get_tdma_slot(ch1),
                }
            return None

        elif opcode == 0x34:  # iden_up_vu (VHF/UHF)
            iden = (tsbk >> 76) & 0xf
            bwvu = (tsbk >> 72) & 0xf
            toff0 = (tsbk >> 58) & 0x3fff
            spac = (tsbk >> 48) & 0x3ff
            freq = (tsbk >> 16) & 0xffffffff
            toff_sign = (toff0 >> 13) & 1
            toff = toff0 & 0x1fff
            if toff_sign == 0:
                toff = 0 - toff
            self.freq_table[iden] = {
                'offset': toff * spac * 125,
                'step': spac * 125,
                'frequency': freq * 5,
            }
            return {
                "type": "iden_up",
                "opcode": opcode,
                "nac": nac,
                "iden": iden,
                "tgid": None,
                "frequency": freq * 5,
                "srcaddr": None,
                "channel_id": None,
            }

        elif opcode == 0x33:  # iden_up_tdma
            mfrid = (tsbk >> 80) & 0xff
            if mfrid != 0:
                return None
            iden = (tsbk >> 76) & 0xf
            channel_type = (tsbk >> 72) & 0xf
            toff0 = (tsbk >> 58) & 0x3fff
            spac = (tsbk >> 48) & 0x3ff
            toff_sign = (toff0 >> 13) & 1
            toff = toff0 & 0x1fff
            if toff_sign == 0:
                toff = 0 - toff
            f1 = (tsbk >> 16) & 0xffffffff
            slots_per_carrier = [1, 1, 1, 2, 4, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]
            self.freq_table[iden] = {
                'offset': toff * spac * 125,
                'step': spac * 125,
                'frequency': f1 * 5,
                'tdma': slots_per_carrier[channel_type],
            }
            return {
                "type": "iden_up",
                "opcode": opcode,
                "nac": nac,
                "iden": iden,
                "tgid": None,
                "frequency": f1 * 5,
                "srcaddr": None,
                "channel_id": None,
                "tdma_slots": slots_per_carrier[channel_type],
            }

        elif opcode == 0x3d:  # iden_up
            iden = (tsbk >> 76) & 0xf
            bw = (tsbk >> 67) & 0x1ff
            toff0 = (tsbk >> 58) & 0x1ff
            spac = (tsbk >> 48) & 0x3ff
            freq = (tsbk >> 16) & 0xffffffff
            toff_sign = (toff0 >> 8) & 1
            toff = toff0 & 0xff
            if toff_sign == 0:
                toff = 0 - toff
            self.freq_table[iden] = {
                'offset': toff * 250000,
                'step': spac * 125,
                'frequency': freq * 5,
            }
            return {
                "type": "iden_up",
                "opcode": opcode,
                "nac": nac,
                "iden": iden,
                "tgid": None,
                "frequency": freq * 5,
                "srcaddr": None,
                "channel_id": None,
            }

        # Opcode not handled
        return {
            "type": "other",
            "opcode": opcode,
            "nac": nac,
            "tgid": None,
            "frequency": None,
            "srcaddr": None,
            "channel_id": None,
        }

    def process_qmsg(self, msg):
        """Process a message from a gr.msg_queue.

        Expects msg.type() == 7 (TSBK). The message payload is 12 bytes:
        2-byte NAC followed by 10-byte TSBK body.

        Args:
            msg: A GNU Radio message object from gr.msg_queue.

        Returns:
            A dict describing the decoded TSBK, or None if the message
            type is not 7 or decoding is not applicable.
        """
        m_type = ctypes.c_int16(msg.type() & 0xffff).value
        if m_type != 7:
            return None

        s = msg.to_string()
        nac = get_ordinals(s[:2])
        if nac == 0xffff:
            # NAC 0xffff indicates voice-channel derived TSBK; we need
            # the real NAC from elsewhere, so skip for now.
            return None

        body = s[2:]
        tsbk = get_ordinals(body)
        return self.decode_tsbk(tsbk, nac)
