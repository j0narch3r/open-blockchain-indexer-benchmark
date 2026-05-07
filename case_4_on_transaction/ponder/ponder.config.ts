import { createConfig } from "ponder";

export default createConfig({
  chains: {
    mainnet: {
      id: 1,
      rpc: process.env.PONDER_RPC_URL_1,
    },
  },
  blocks: {
    ethereum: {
      chain: "mainnet",
      interval: 1,
      startBlock: 22280000,
      endBlock: 22290000,
    }
  },
});