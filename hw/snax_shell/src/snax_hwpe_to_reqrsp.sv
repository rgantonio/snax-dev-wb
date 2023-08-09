//---------------------------------------------
// Copyright 2023 Katolieke Universiteit Leuven (KUL)
// Solderpad Hardware License, Version 0.51, see LICENSE for details.
// SPDX-License-Identifier: SHL-0.51
// Author: Ryan Antonio (ryan.antonio@kuleuven.be)
//---------------------------------------------

// verilog_lint: waive-start line-length
// verilog_lint: waive-start no-trailing-spaces

import reqrsp_pkg::*;

module snax_hwpe_to_reqrsp #(
  parameter int unsigned AddrWidth = 48,
  parameter int unsigned DataWidth = 64,
  parameter type tcdm_req_t = logic,            // Memory request payload type, usually write enable, write data, etc.
  parameter type tcdm_rsp_t = logic             // Memory response payload type, usually read data
)(
  input  logic                  clk_i,          // Clock
  input  logic                  rst_ni,         // Asynchronous reset, active low
  output tcdm_req_t             tcdm_req_o,     // TCDM valid ready format
  input  tcdm_rsp_t             tcdm_rsp_i,     // TCDM valid ready format
  hwpe_stream_intf_tcdm.slave   hwpe_tcdm_slave // periph slave port
);

  //---------------------------------------------
  // Some local parameters
  //---------------------------------------------

  localparam int unsigned StrbWidth = (DataWidth/8);

  //---------------------------------------------
  // Pack, unpack, and some logic
  //---------------------------------------------
  logic push_hwpe_tcdm;
  logic pop_hwpe_tcdm;

  logic fifo_hwpe_tcdm_full;
  logic fifo_hwpe_tcdm_empty;

  logic be;
  logic strb;

  logic [31:0] unpack_addr;
  logic [31:0] unpack_data;

  typedef struct packed {
    logic [31:0] add;
    logic        wen;
    logic        be;
    logic [31:0] data;
    logic        valid;
  } hwpe_tcdm_t;

  hwpe_tcdm_t fifo_hwpe_tcdm_data_in;
  hwpe_tcdm_t fifo_hwpe_tcdm_data_out;

  // HWPE shows 4 bits of be but let's just say it's always strobed properly
  assign be = hwpe_tcdm_slave.be[0];

  // Pack
  assign fifo_hwpe_tcdm_data_in.add   = hwpe_tcdm_slave.add;
  assign fifo_hwpe_tcdm_data_in.wen   = !hwpe_tcdm_slave.wen; // Not wen, because HWPE uses wen=0 to write and wen = 1 to read but memory uses wen = 1 to write and wen = 0 to read
  assign fifo_hwpe_tcdm_data_in.be    = be;
  assign fifo_hwpe_tcdm_data_in.data  = hwpe_tcdm_slave.data;
  assign fifo_hwpe_tcdm_data_in.valid = hwpe_tcdm_slave.gnt & hwpe_tcdm_slave.req;

  // Unpack
  assign unpack_addr        = fifo_hwpe_tcdm_data_out.add;
  assign tcdm_req_o.q.write = fifo_hwpe_tcdm_data_out.wen;
  assign strb               = fifo_hwpe_tcdm_data_out.be;
  assign unpack_data        = fifo_hwpe_tcdm_data_out.data;

  // This is necessary to include the empty. Since the FIFO does not clear its contents,
  // we need to disable the valid when last state of FIFO was released and next state is empty
  assign tcdm_req_o.q_valid = fifo_hwpe_tcdm_data_out.valid & !fifo_hwpe_tcdm_empty; 

  //---------------------------------------------
  // Simple grant request control
  // Whenever a request arrives, we grant it immediately
  // Load unto a fifo buffer the requests
  // Grant only when fifo buffer is not full also!
  //---------------------------------------------

  always_ff @ (posedge clk_i or negedge rst_ni) begin
      if(!rst_ni) begin
        hwpe_tcdm_slave.gnt <= 1'b0;
      end else begin
        hwpe_tcdm_slave.gnt <= hwpe_tcdm_slave.req & !fifo_hwpe_tcdm_full;
      end
  end

  // We always push whenever a request and gnt comes
  // And when the fifo is not full!
  assign push_hwpe_tcdm = hwpe_tcdm_slave.req & hwpe_tcdm_slave.gnt & !fifo_hwpe_tcdm_full;

  // Pop when port has a valid transaction at the tcdm side
  // The not empty signal is mandatory because the fifo_v3 loops around
  // Then loads the last state on the data_out. Whenever a pop happens, fifo does not
  // clear the contents at a specific pointer
  assign pop_hwpe_tcdm = tcdm_req_o.q_valid & tcdm_rsp_i.q_ready;

  //---------------------------------------------
  // FIFO queue for tranasctions from HWPE to TCDM
  //---------------------------------------------
  fifo_v3 #(
    .dtype      ( hwpe_tcdm_t             ), // Sum of address and 
    .DEPTH      ( 8                       )  // Arbitrarily chosen
  ) i_hwpe_tcdm_fifo (
    .clk_i      ( clk_i                   ),
    .rst_ni     ( rst_ni                  ),
    .flush_i    ( 1'b0                    ),
    .testmode_i ( 1'b0                    ),
    .full_o     ( fifo_hwpe_tcdm_full     ),
    .empty_o    ( fifo_hwpe_tcdm_empty    ),
    .usage_o    ( /*unused*/              ),
    .data_i     ( fifo_hwpe_tcdm_data_in  ),
    .push_i     ( push_hwpe_tcdm          ),
    .data_o     ( fifo_hwpe_tcdm_data_out ),
    .pop_i      ( pop_hwpe_tcdm           )
  );

  //---------------------------------------------
  // We just directly map the tcdm_rsp_i to the HWPE ports
  //---------------------------------------------
  assign hwpe_tcdm_slave.r_data  = tcdm_rsp_i.p.data;
  assign hwpe_tcdm_slave.r_valid = tcdm_rsp_i.p_valid;

  //---------------------------------------------
  // Some signals are unimportant so we tie them to 0
  // Strb is just extended version of strb
  //---------------------------------------------
  assign tcdm_req_o.q.addr = {{31{1'b0}},unpack_addr};
  assign tcdm_req_o.q.data = {{31{1'b0}},unpack_data};
  assign tcdm_req_o.q.amo  = AMONone;
  assign tcdm_req_o.q.strb = {StrbWidth{strb}};
  assign tcdm_req_o.q.user   = '0;

// verilog_lint: waive-stop line-length
// verilog_lint: waive-stop no-trailing-spaces

endmodule


/* ------------ MODULE USAGE ---------------
snax_hwpe_to_reqrsp #(
  .DataWidth        ( DataWidth       ),  // Data width to use
  .tcdm_req_t       ( tcdm_req_t      ),  // TCDM request type
  .tcdm_rsp_t       ( tcdm_rsp_t      )   // TCDM response type
) i_snax_hwpe_to_reqrsp (
  .clk_i            ( clk_i           ),  // Clock
  .rst_ni           ( rst_ni          ),  // Asynchronous reset, active low
  .tcdm_req_o       ( tcdm_req_o      ),  // TCDM valid ready format
  .tcdm_rsp_i       ( tcdm_rsp_i      ),  // TCDM valid ready format
  .hwpe_tcdm_slave  ( hwpe_tcdm_slave )   // HWPE TCDM slave port
);
--------------- MODULE USAGE ------------ */
