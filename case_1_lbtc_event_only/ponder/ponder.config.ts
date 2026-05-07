import { createConfig } from "ponder";

import { LBTCAbi } from "./abis/LBTCAbi";

export default createConfig({
  chains: {
    mainnet: {
      id: 1,
      rpc: process.env.PONDER_RPC_URL_1,
    },
  },
  contracts: {
    LBTC: {
      chain: "mainnet",
      abi: LBTCAbi,
      address: "0x8236a87084f8B84306f72007F36F2618A5634494",
      startBlock: 0,
      endBlock: 22200000
    }
  },
});
