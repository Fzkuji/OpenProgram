/**
 * Register-flow pickers — the two-step token register form the
 * /channel command opens for channel accounts. Token providers ask
 * for a bot token after the account id; WeChat starts the QR-login
 * flow directly after the account id.
 */
import React from 'react';
import { LineInput } from '../../../components/LineInput.js';
import { randomLocalId } from '../helpers.js';
import type { PickerCtx } from '../pickerRouter.js';

type RegisterKind = 'register_account_id' | 'register_token';

export function buildRegisterPicker(
  ctx: PickerCtx,
  kind: RegisterKind,
): React.ReactElement | null {
  const {
    client, pushSystem,
    conversationId, registerForm,
    setPickerKind, setRegisterForm,
    setChosenChannel, setChosenAccount, setConversationId,
    setQrAscii, setQrStatus,
  } = ctx;

  if (kind === 'register_account_id') {
    return (
      <LineInput
        label={`Register ${registerForm.channel ?? '?'} account`}
        hint="Choose a short id you'll use to refer to this account (e.g. 'default', 'work')."
        initial="default"
        onSubmit={(v) => {
          const id = v.trim();
          if (!id) {
            pushSystem('account_id required.');
            return;
          }
          setRegisterForm((f) => ({ ...f, accountId: id }));
          if (registerForm.channel === 'wechat') {
            setChosenAccount(id);
            setQrAscii(undefined);
            setQrStatus('Requesting QR code…');
            client.send({
              action: 'start_channel_login',
              channel: 'wechat',
              account_id: id,
            } as never);
            setRegisterForm({});
            setPickerKind('channel_qr_wait');
            return;
          }
          setPickerKind('register_token');
        }}
        onCancel={() => {
          setPickerKind('channel_account');
          setRegisterForm({});
        }}
      />
    );
  }

  if (kind === 'register_token') {
    return (
      <LineInput
        label={`${registerForm.channel ?? '?'} bot token for "${registerForm.accountId}"`}
        hint="Paste the bot token from your provider dashboard."
        mask
        onSubmit={(token) => {
          const t = token.trim();
          if (!t) {
            pushSystem('token required.');
            return;
          }
          if (!registerForm.channel || !registerForm.accountId) {
            pushSystem('register form incomplete; aborting.');
            setPickerKind(null);
            setRegisterForm({});
            return;
          }
          client.send({
            action: 'add_channel_account',
            channel: registerForm.channel,
            account_id: registerForm.accountId,
            token: t,
          });
          // Same one-step semantics as wechat QR done: token saved
          // → bind the current TUI conversation as catch-all. Mint
          // a conv id if none exists so the user doesn't need to
          // send a dummy message first.
          if (registerForm.channel && registerForm.accountId) {
            const targetConvId = conversationId ?? `local_${randomLocalId()}`;
            if (!conversationId) {
              setConversationId(targetConvId);
            }
            client.send({
              action: 'attach_session',
              session_id: targetConvId,
              channel: registerForm.channel,
              account_id: registerForm.accountId,
              peer_kind: 'direct',
              peer_id: '*',
            } as never);
            pushSystem(
              `✅ Registered ${registerForm.channel}:${registerForm.accountId} ` +
              `and bound this conversation to receive inbound messages.`,
            );
          }
          setPickerKind(null);
          setRegisterForm({});
          setChosenChannel(undefined);
          client.send({ action: 'list_channel_accounts' });
        }}
        onCancel={() => setPickerKind('register_account_id')}
      />
    );
  }

  return null;
}
