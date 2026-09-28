import {createPublicClient} from '@polymarket/client';

export async function resolveAccountWallet(signerAddress, requestedWallet = '', publicClient = createPublicClient()) {
  const profile = await publicClient.fetchPublicProfile({address: signerAddress});
  const profileWallet = profile?.wallet || '';
  if (requestedWallet && profileWallet && requestedWallet.toLowerCase() !== profileWallet.toLowerCase()) {
    throw new Error('Requested wallet differs from the Polymarket account wallet for this signer.');
  }
  // The SDK derives a new signer's Deposit Wallet when no profile exists.
  return requestedWallet || profileWallet || undefined;
}
