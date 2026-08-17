/**
 * Channel-flow pickers — the 6 picker states the /channel command
 * walks the user through:
 *   channel → channel_account → channel_action
 *     → channel_peer_input → channel_overwrite_confirm
 * plus the read-only channel_qr_wait card the WeChat QR-login flow
 * renders while waiting for a phone scan.
 *
 * Split out of pickerRouter.tsx because these six branches alone
 * dominate the router file (~340 lines of 600). The dispatcher in
 * pickerRouter delegates to buildChannelPicker; this file owns all
 * binding/overwrite/QR wiring in one place.
 */
import React from 'react';
import { Box, Text } from '../../../runtime/index';
import { Picker, PickerItem } from '../../../components/Picker.js';
import { LineInput } from '../../../components/LineInput.js';
import { findExistingAlias, randomLocalId } from '../helpers.js';
import type { PickerCtx } from '../pickerRouter.js';
import type { PickerKind } from '../types.js';

type ChannelKind =
  | 'channel' | 'channel_account' | 'channel_action'
  | 'channel_peer_input' | 'channel_overwrite_confirm' | 'channel_qr_wait';

export function buildChannelPicker(
  ctx: PickerCtx,
  kind: ChannelKind,
): React.ReactElement | null {
  const {
    client, colors, pushSystem,
    pendingAttach,
    chosenChannel, chosenAccount, conversationId,
    channelAccounts,
    qrAscii, qrStatus,
    setPickerKind, setPendingAttach,
    setChosenChannel, setChosenAccount, setConversationId,
    setQrAscii, setQrStatus, setRegisterForm,
    sessionAliasesRef,
  } = ctx;

  if (kind === 'channel') {
    const channels = ['wechat', 'telegram', 'discord', 'slack'];
    const items: PickerItem<string>[] = channels.map((ch) => ({
      label: ch,
      description:
        channelAccounts.filter((a) => a.channel === ch).length > 0
          ? `${channelAccounts.filter((a) => a.channel === ch).length} account(s) configured`
          : 'no account yet',
      value: ch,
    }));
    return (
      <Picker
        title="Choose a channel"
        items={items}
        onSelect={(it) => {
          setChosenChannel(it.value);
          setPickerKind('channel_account');
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (kind === 'channel_account') {
    const filtered = channelAccounts.filter((a) => a.channel === chosenChannel);
    const accountItems: PickerItem<string>[] = filtered.map((a) => {
      // Surface the current catch-all binding (if any) in the row,
      // so the user sees "selecting this will overwrite X" before
      // pressing Enter — the confirm picker is the safety net,
      // this is the front-page warning.
      const bound = findExistingAlias(
        sessionAliasesRef.current,
        chosenChannel ?? '', a.account_id ?? '', '*',
      );
      const status = a.configured ? 'logged in' : 'not configured';
      return {
        label: a.account_id ?? '',
        description: bound ? `${status} · already bound → ${bound}` : status,
        value: a.account_id ?? '',
      };
    });
    const isTokenChannel =
      chosenChannel === 'discord' || chosenChannel === 'telegram' || chosenChannel === 'slack';
    const items: PickerItem<string>[] = [
      ...accountItems,
      isTokenChannel
        ? { label: '+ Register new', description: 'paste a bot token to add an account', value: '__register__' }
        : { label: '+ Register new', description: 'scan a WeChat QR code in this TUI', value: '__register_wechat__' },
    ];
    return (
      <Picker
        title={`Pick a ${chosenChannel} account`}
        items={items}
        onSelect={(it) => {
          if (it.value === '__register__') {
            setRegisterForm({ channel: chosenChannel });
            setPickerKind('register_account_id');
            return;
          }
          if (it.value === '__register_wechat__') {
            setRegisterForm({ channel: chosenChannel });
            setPickerKind('register_account_id');
            return;
          }
          // Existing account: bind the current TUI conversation as
          // catch-all in one keypress. Mint a fresh conv id if there
          // isn't one yet — server-side attach_session lazy-creates
          // an empty SessionDB row.
          const targetConvId = conversationId ?? `local_${randomLocalId()}`;
          const okMsg =
            `✅ Bound this conversation to ${chosenChannel}:${it.value}. ` +
            `Every inbound message lands here. Tweak via /bindings.`;
          // Detect overwrite ahead of time. Server's attach() does
          // return ``replaced`` so we *would* learn post-hoc — but
          // the user has zero chance to say no at that point.
          // Surfacing it pre-attach turns a silent destructive op
          // into an explicit choice.
          const existing = findExistingAlias(
            sessionAliasesRef.current,
            chosenChannel ?? '', it.value, '*',
          );
          if (existing && existing !== targetConvId) {
            if (!conversationId) setConversationId(targetConvId);
            setPendingAttach({
              channel: chosenChannel ?? '',
              account_id: it.value,
              peer_kind: 'direct',
              peer_id: '*',
              session_id: targetConvId,
              existingSessionId: existing,
              successMessage: okMsg,
            });
            setPickerKind('channel_overwrite_confirm');
            return;
          }
          if (!conversationId) setConversationId(targetConvId);
          client.send({
            action: 'attach_session',
            session_id: targetConvId,
            channel: chosenChannel,
            account_id: it.value,
            peer_kind: 'direct',
            peer_id: '*',
          } as never);
          pushSystem(okMsg);
          setPickerKind(null);
          setChosenChannel(undefined);
          setChosenAccount(undefined);
        }}
        onCancel={() => setPickerKind('channel')}
      />
    );
  }

  if (kind === 'channel_action') {
    // Three-way: catch-all to current conversation, specific peer,
    // or just list/delete existing bindings. Catch-all means every
    // inbound message on this channel:account lands in conversationId
    // — useful for "I want all my wechat replies in this TUI session".
    const items: PickerItem<string>[] = [
      {
        label: 'Bind ALL inbound to this conversation',
        description: conversationId
          ? `Every ${chosenChannel}:${chosenAccount} message → current chat`
          : `Every ${chosenChannel}:${chosenAccount} message → a fresh chat (auto-created)`,
        value: '__catchall__',
      },
      {
        label: 'Bind a specific peer to this conversation',
        description: 'You will be prompted for the peer id (wxid_xxx etc.)',
        value: '__peer__',
      },
      {
        label: 'Show existing bindings',
        description: 'List + remove rules later',
        value: '__list__',
      },
    ];
    return (
      <Picker
        title={`Bind ${chosenChannel}:${chosenAccount} how?`}
        items={items}
        onSelect={(it) => {
          if (it.value === '__list__') {
            client.send({ action: 'list_session_aliases' } as never);
            client.send({ action: 'list_channel_bindings' } as never);
            setPickerKind(null);
            setChosenChannel(undefined);
            setChosenAccount(undefined);
            return;
          }
          if (it.value === '__catchall__') {
            // Catch-all = attach with peer_id="*". The bindings/route
            // logic falls through to alias.lookup which matches any
            // peer for this (channel, account) when peer_id == "*".
            // Lazy-create the TUI conversation if there isn't one yet
            // — server-side attach_session backs it with an empty
            // SessionDB row.
            const targetConvId = conversationId ?? `local_${randomLocalId()}`;
            const okMsg =
              `✅ Bound ${chosenChannel}:${chosenAccount} (catch-all) → current conversation. ` +
              `Channel worker will route every inbound message here.`;
            const existing = findExistingAlias(
              sessionAliasesRef.current,
              chosenChannel ?? '', chosenAccount ?? '', '*',
            );
            if (existing && existing !== targetConvId) {
              if (!conversationId) setConversationId(targetConvId);
              setPendingAttach({
                channel: chosenChannel ?? '',
                account_id: chosenAccount ?? '',
                peer_kind: 'direct',
                peer_id: '*',
                session_id: targetConvId,
                existingSessionId: existing,
                successMessage: okMsg,
              });
              setPickerKind('channel_overwrite_confirm');
              return;
            }
            if (!conversationId) setConversationId(targetConvId);
            client.send({
              action: 'attach_session',
              session_id: targetConvId,
              channel: chosenChannel,
              account_id: chosenAccount,
              peer_kind: 'direct',
              peer_id: '*',
            } as never);
            pushSystem(okMsg);
            setPickerKind(null);
            setChosenChannel(undefined);
            setChosenAccount(undefined);
            return;
          }
          if (it.value === '__peer__') {
            setPickerKind('channel_peer_input');
            return;
          }
        }}
        onCancel={() => setPickerKind('channel_account')}
      />
    );
  }

  if (kind === 'channel_peer_input') {
    return (
      <LineInput
        label={`Peer ID for ${chosenChannel}:${chosenAccount}`}
        hint="e.g. wxid_xxxx for WeChat. The bot's worker log shows them once messages arrive."
        onSubmit={(v) => {
          const peerId = v.trim();
          if (!peerId) {
            pushSystem('peer id required.');
            return;
          }
          const targetConvId = conversationId ?? `local_${randomLocalId()}`;
          const okMsg =
            `✅ Bound ${chosenChannel}:${chosenAccount}:${peerId} → current conversation.`;
          const existing = findExistingAlias(
            sessionAliasesRef.current,
            chosenChannel ?? '', chosenAccount ?? '', peerId,
          );
          if (existing && existing !== targetConvId) {
            if (!conversationId) setConversationId(targetConvId);
            setPendingAttach({
              channel: chosenChannel ?? '',
              account_id: chosenAccount ?? '',
              peer_kind: 'direct',
              peer_id: peerId,
              session_id: targetConvId,
              existingSessionId: existing,
              successMessage: okMsg,
            });
            setPickerKind('channel_overwrite_confirm');
            return;
          }
          if (!conversationId) setConversationId(targetConvId);
          client.send({
            action: 'attach_session',
            session_id: targetConvId,
            channel: chosenChannel,
            account_id: chosenAccount,
            peer_kind: 'direct',
            peer_id: peerId,
          } as never);
          pushSystem(okMsg);
          setPickerKind(null);
          setChosenChannel(undefined);
          setChosenAccount(undefined);
        }}
        onCancel={() => setPickerKind('channel_action')}
      />
    );
  }

  if (kind === 'channel_overwrite_confirm') {
    // Two-option confirm: replace the existing alias or back out.
    // Putting the actual session ids in the title makes the
    // destructive op visible — same model Linear uses for
    // "delete issue X" prompts.
    const p = pendingAttach;
    if (!p) return null;
    return (
      <Picker
        title={
          `${p.channel}:${p.account_id}:${p.peer_id} is already bound to ` +
          `${p.existingSessionId}. Replace with ${p.session_id}?`
        }
        items={[
          {
            label: 'Replace existing binding',
            description: `${p.existingSessionId} → ${p.session_id}`,
            value: '__yes__',
          },
          {
            label: 'Cancel',
            description: 'Keep the existing binding, do nothing',
            value: '__no__',
          },
        ]}
        onSelect={(it) => {
          if (it.value === '__yes__') {
            client.send({
              action: 'attach_session',
              session_id: p.session_id,
              channel: p.channel,
              account_id: p.account_id,
              peer_kind: p.peer_kind,
              peer_id: p.peer_id,
            } as never);
            pushSystem(p.successMessage);
          } else {
            pushSystem(
              `Cancelled. ${p.channel}:${p.account_id}:${p.peer_id} ` +
              `still bound to ${p.existingSessionId}.`,
            );
          }
          setPendingAttach(null);
          setPickerKind(null);
          setChosenChannel(undefined);
          setChosenAccount(undefined);
        }}
        onCancel={() => {
          pushSystem(
            `Cancelled. ${p.channel}:${p.account_id}:${p.peer_id} ` +
            `still bound to ${p.existingSessionId}.`,
          );
          setPendingAttach(null);
          setPickerKind(null);
          setChosenChannel(undefined);
          setChosenAccount(undefined);
        }}
      />
    );
  }

  if (kind === 'channel_qr_wait') {
    // Read-only "picker" — no input, just renders QR + status until
    // the qr_login envelope handler advances us out.
    return (
      <Box flexDirection="column" borderStyle="single" paddingX={1} paddingY={0}>
        <Text bold>Scan to log in to {chosenChannel}</Text>
        <Text color={colors.channelQr.hint}>Open WeChat on your phone → tap [+] → "Scan QR"</Text>
        {qrAscii ? <Text>{qrAscii}</Text> : <Text color={colors.channelQr.hint}>Loading QR…</Text>}
        <Text color={colors.channelQr.status}>{qrStatus ?? ''}</Text>
        <Text color={colors.channelQr.hint}>(esc to cancel)</Text>
      </Box>
    );
  }

  return null;
}
