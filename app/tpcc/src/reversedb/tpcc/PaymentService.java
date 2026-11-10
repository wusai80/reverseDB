package reversedb.tpcc;

import java.math.BigDecimal;
import java.sql.*;

/** Explicit customer-ID payment; caller controls transaction and clock. */
public final class PaymentService {
  public void pay(Connection c,int district,int customerDistrict,int customer,BigDecimal input,long clock)
      throws SQLException {
    // Preserve the reference's float arithmetic until a separate money contract is agreed.
    float amount=input.floatValue();
    Sql.one(c,"UPDATE warehouse SET w_ytd=w_ytd+? WHERE w_id=1",BigDecimal.valueOf(amount));
    String warehouseName;
    try(PreparedStatement s=Sql.bind(c,"SELECT w_name FROM warehouse WHERE w_id=1");ResultSet r=s.executeQuery()) {
      if(!r.next()) throw new SQLException("Missing warehouse");
      warehouseName=r.getString(1);
    }
    Sql.one(c,"UPDATE district SET d_ytd=d_ytd+? WHERE d_w_id=1 AND d_id=?",BigDecimal.valueOf(amount),district);
    String districtName;
    try(PreparedStatement s=Sql.bind(c,"SELECT d_name FROM district WHERE d_w_id=1 AND d_id=?",district);ResultSet r=s.executeQuery()) {
      if(!r.next()) throw new SQLException("Missing district");
      districtName=r.getString(1);
    }
    float balance,ytd; int payments; String credit;
    try(PreparedStatement s=Sql.bind(c,"SELECT c_balance,c_ytd_payment,c_payment_cnt,c_credit FROM customer WHERE c_w_id=1 AND c_d_id=? AND c_id=?",customerDistrict,customer);
        ResultSet r=s.executeQuery()) {
      if(!r.next()) throw new SQLException("Missing customer");
      balance=r.getFloat(1)-amount; ytd=r.getFloat(2)+amount;
      payments=r.getInt(3)+1; credit=r.getString(4);
    }
    if(credit.equals("BC")) {
      String data;
      try(PreparedStatement s=Sql.bind(c,"SELECT c_data FROM customer WHERE c_w_id=1 AND c_d_id=? AND c_id=?",customerDistrict,customer);
          ResultSet r=s.executeQuery()) {
        if(!r.next()) throw new SQLException("Missing customer data");
        data=customer+" "+customerDistrict+" 1 "+district+" 1 "+amount+" | "+r.getString(1);
      }
      data=data.substring(0,Math.min(500,data.length()));
      Sql.one(c,"UPDATE customer SET c_balance=?,c_ytd_payment=?,c_payment_cnt=?,c_data=? WHERE c_w_id=1 AND c_d_id=? AND c_id=?",
          (double)balance,(double)ytd,payments,data,customerDistrict,customer);
    } else {
      Sql.one(c,"UPDATE customer SET c_balance=?,c_ytd_payment=?,c_payment_cnt=? WHERE c_w_id=1 AND c_d_id=? AND c_id=?",
          (double)balance,(double)ytd,payments,customerDistrict,customer);
    }
    String data=warehouseName.substring(0,Math.min(10,warehouseName.length()))+"    "+districtName.substring(0,Math.min(10,districtName.length()));
    Sql.one(c,"INSERT INTO history(h_c_d_id,h_c_w_id,h_c_id,h_d_id,h_w_id,h_date,h_amount,h_data) VALUES(?,1,?,?,1,?,?,?)",
        customerDistrict,customer,district,new Timestamp(clock),(double)amount,data);
  }
}
